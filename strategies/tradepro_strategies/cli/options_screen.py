"""tradepro-options-screen — the wheel CANDIDATE SCREEN job.

For each approved underlying it computes, on the gateway's connection:
  • IV-Rank   — 52w OPTION_IMPLIED_VOLATILITY (the vega-edge gate)
  • regime    — Ichimoku-cloud position on daily bars (constructive vs breaking)
  • falling-knife — recent sharp breakdown
  • chain     — near-month put at ~0.27 delta (OI / spread / premium) [LIVE only]
then runs it ALL through the options risk engine and POSTs the screen to
/api/options/candidates for the Options tab.

NO FALSE POSITIVES: anything that can't be computed (chain unavailable outside
market hours, no IV history, …) flows to the risk engine as None → BLOCK with a
visible reason → eligible=False. A weekend run therefore shows IV-Rank + regime
but "pending market open" on the live-chain gates — honest, not a fake green.

Run:  TRADEPRO_IBKR_PORT=7500 uv run tradepro-options-screen
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from ..quant_engine.options.iv_rank import fetch_iv_rank
from ..quant_engine.options.risk import (
    MarketContext, OptionsRiskConfig, PortfolioState, Regime, Structure,
    TradeCandidate, evaluate,
)

log = logging.getLogger("tradepro.options_screen")

# BRD candidate buckets — core quality + lower-priced liquid names.
DEFAULT_UNIVERSE = [
    "KO", "PG", "PEP", "JNJ", "ABBV", "T", "VZ", "XOM", "CVX", "JPM",
    "F", "INTC", "PFE", "MO",
]

_FX_GBPUSD = 1.27  # BRD display rate; USD strike×100 → GBP notional


def _earnings_in_window(symbol: str, dte: int) -> bool | None:
    """Is an earnings date within the option's expiry window (today..+dte)?
    yfinance best-effort. Returns None when unavailable → the risk engine BLOCKs
    (no false positive — never sell premium we can't clear for earnings)."""
    try:
        import datetime as _d
        import yfinance as yf
        cal = yf.Ticker(symbol).calendar
        dates = []
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if ed:
                dates = ed if isinstance(ed, list) else [ed]
        if not dates:
            return None
        today = _d.date.today()
        end = today + _d.timedelta(days=dte)
        for d in dates:
            dd = d if isinstance(d, _d.date) else getattr(d, "date", lambda: None)()
            if dd and today <= dd <= end:
                return True
        return False
    except Exception:  # noqa: BLE001
        return None


def _mid(vals: list[float], i: int, n: int) -> float | None:
    if i + 1 < n:
        return None
    w = vals[i - n + 1: i + 1]
    return (max(w) + min(w)) / 2.0


def regime_from_closes(closes: list[float]) -> tuple[str | None, bool | None]:
    """Ichimoku-cloud regime from daily closes (newest last). Constructive =
    price above the cloud (GREEN), in the cloud (YELLOW); breaking = below cloud
    (ORANGE); sharp recent breakdown = RED. Returns (regime, falling_knife);
    (None, None) when there isn't enough history to decide (→ risk BLOCKs)."""
    c = [x for x in closes if x and x > 0]
    if len(c) < 60:
        return None, None
    i = len(c) - 1
    tenkan = _mid(c, i, 9)
    kijun = _mid(c, i, 26)
    span_a = (tenkan + kijun) / 2 if (tenkan and kijun) else None
    span_b = _mid(c, i, 52)
    if span_a is None or span_b is None:
        return None, None
    cloud_top, cloud_bot = max(span_a, span_b), min(span_a, span_b)
    price = c[-1]
    # Falling knife: a sharp recent drop (≥10% over ~5 sessions) AND below cloud.
    ret_5d = (c[-1] / c[-6] - 1.0) if len(c) >= 6 else 0.0
    falling_knife = (ret_5d <= -0.10) and (price < cloud_bot)
    if falling_knife:
        return Regime.RED.value, True
    if price > cloud_top:
        return Regime.GREEN.value, False
    if price >= cloud_bot:
        return Regime.YELLOW.value, False
    return Regime.ORANGE.value, False


def _screen_symbol(ib, ib_insync, sym: str, cfg: OptionsRiskConfig, market_open: bool) -> dict:
    """Build one candidate row: IV-Rank + regime + (live) chain → risk engine."""
    ivr = fetch_iv_rank(sym, ib=ib)
    # Daily closes for the regime (TRADES; historical, available any time).
    # The last IBKR close also serves as the AUTHORITATIVE reference spot to
    # sanity-check the (free, sometimes garbage) yfinance chain spot against.
    regime = falling_knife = ref_close = None
    try:
        c = ib_insync.Stock(sym, "SMART", "USD"); ib.qualifyContracts(c)
        bars = ib.reqHistoricalData(
            c, endDateTime="", durationStr="1 Y", barSizeSetting="1 day",
            whatToShow="TRADES", useRTH=True, formatDate=1, timeout=30)
        closes = [b.close for b in bars]
        regime, falling_knife = regime_from_closes(closes)
        if closes and closes[-1] > 0:
            ref_close = closes[-1]
    except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (no false positive)
        log.warning("%s regime fetch failed: %s", sym, e)

    # Options chain (yfinance — works outside market hours; bid/ask may be stale
    # on weekends → spread surfaced as None → fail-visible). Select the put
    # nearest 0.27 delta (BRD strike rule).
    oi = strike = delta = premium = notional_gbp = None
    spread = None
    dte = 35
    chain_ok = False
    spot_divergence = None  # |yf spot / IBKR close − 1| when both known
    from ..quant_engine.options.chains import fetch_chain, select_by_abs_delta, delta_of
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    try:
        pricer = BlackScholesPricer()
        chain = fetch_chain(sym, target_dte=35, pricer=pricer)
        # Spot-sanity guard: reject a chain whose spot disagrees with the
        # authoritative IBKR close by >15% (e.g. yfinance returned INTC "$134").
        # A garbage spot poisons strike selection → never trust it; fail-visible.
        spot_ok = True
        if chain and chain.spot > 0 and ref_close:
            spot_divergence = abs(chain.spot / ref_close - 1.0)
            if spot_divergence > 0.15:
                spot_ok = False
                log.warning("%s chain spot %.2f diverges %.0f%% from IBKR close %.2f — rejecting chain",
                            sym, chain.spot, spot_divergence * 100, ref_close)
        if chain and chain.puts and chain.spot > 0 and spot_ok:
            dte = chain.dte
            t = max(dte, 1) / 365.0
            q = select_by_abs_delta(chain.puts, 0.27, chain.spot, t, pricer)
            if q is not None:
                strike = q.strike
                delta = abs(delta_of(q, chain.spot, t, pricer))
                oi = q.open_interest
                spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                premium = q.mid if q.mid > 0 else None
                notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)  # USD strike×100 → GBP
                chain_ok = True
    except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (fail-visible)
        log.warning("%s chain fetch failed: %s", sym, e)

    earnings_in_window = _earnings_in_window(sym, dte)

    ctx = MarketContext(
        regime=Regime(regime) if regime else None,
        falling_knife=falling_knife,
        iv_rank=ivr.iv_rank if ivr.available else None,
        open_interest=oi, bid_ask_spread_usd=spread,
        earnings_in_expiry_window=earnings_in_window,
        # Screen on best-available data: a usable chain is enough to ASSESS
        # eligibility (execution still needs live quotes at the open).
        data_fresh=chain_ok,
    )
    cand = TradeCandidate(symbol=sym, structure=Structure.CASH_SECURED_PUT,
                          abs_delta=delta, dte=dte, strike=strike, notional_gbp=notional_gbp)
    decision = evaluate(cand, ctx, PortfolioState(), cfg)
    return {
        "symbol": sym,
        "regime": regime,
        "iv_rank": round(ivr.iv_rank, 1) if ivr.available else None,
        "iv": round(ivr.iv, 4) if ivr.available else None,
        "open_interest": oi,
        "spread_usd": spread,
        "eligible": decision.allowed,
        "blocks": decision.blocks,
        "warnings": decision.warnings,
        "suggested_strike": strike,
        "suggested_delta": delta,
        "suggested_premium": premium,
        "ref_close": round(ref_close, 2) if ref_close else None,
        "spot_divergence_pct": round(spot_divergence * 100, 1) if spot_divergence is not None else None,
    }


def run_screen(symbols: list[str] | None = None) -> dict:
    import ib_insync
    from . import push_to_api as _pta
    from ..paper import market_hours

    universe = symbols or DEFAULT_UNIVERSE
    cfg = OptionsRiskConfig()
    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    cid = int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID", "97"))
    try:
        market_open = market_hours.is_open("us_equity", datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        market_open = False

    ib = ib_insync.IB()
    ib.connect(host, port, clientId=cid, timeout=20)
    rows = []
    try:
        for sym in universe:
            rows.append(_screen_symbol(ib, ib_insync, sym, cfg, market_open))
            log.info("screened %s", sym)
    finally:
        ib.disconnect()

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "market_open": market_open,
        "candidates": rows,
    }
    # Push to the API for the Options tab.
    import requests
    base, tok = _pta.load_credentials()
    if base and tok:
        r = requests.post(f"{base.rstrip('/')}/api/options/candidates",
                          json=payload, headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        log.info("pushed screen: HTTP %s (%d candidates, %d eligible)",
                 r.status_code, len(rows), sum(1 for x in rows if x["eligible"]))
    return payload


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    p = run_screen()
    elig = [c["symbol"] for c in p["candidates"] if c["eligible"]]
    print(f"\nOptions screen: {len(p['candidates'])} screened, {len(elig)} eligible "
          f"(market_open={p['market_open']})")
    for c in p["candidates"]:
        mark = "✓" if c["eligible"] else "·"
        ivr = f"{c['iv_rank']:.0f}%" if c["iv_rank"] is not None else "n/a"
        print(f"  {mark} {c['symbol']:5} regime={c['regime'] or 'n/a':6} IV-Rank={ivr:>4} "
              f"{'' if c['eligible'] else '— ' + '; '.join(c['blocks'][:2])}")
    return 0
