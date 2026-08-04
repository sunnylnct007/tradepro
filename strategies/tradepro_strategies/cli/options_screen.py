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

# Wheel universe — EVIDENCE-SELECTED from a 2023→2026 wheel backtest (return on
# capital-at-risk vs the ~4.5%/yr bank rate). The winners are mid-vol QUALITY
# names (energy / utility / healthcare / staples): they beat the bank ~2× with
# low drawdown. Dropped vs the old BRD list: KO/T/JPM (under the bank — too
# low-vol, premiums too thin), INTC/PFE (beat bank but −50%+ drawdown). Add
# DUK/D for defensive yield. Re-run tradepro-wheel-backtest to revisit.
#   CVX 12.2%/-7%  ABBV 11.7%/-16%  XOM 11.1%/-8%  VZ 10.3%/-11%
#   DUK 8.8%/-8%   JNJ 6.3%/-10%    MO 5.7%/-10%   PG 5.0%/-13%
# Wheel universe — liquid option chains, quality underlyings you'd accept
# assignment on, weighted toward strikes that FIT the configurable pot
# (TRADEPRO_WHEEL_PER_POSITION_GBP; a $315 JPM strike needs a raised cap).
# Expanded 10 → 30 (user: "only 14 symbols — we should evaluate more").
DEFAULT_UNIVERSE = [
    # original core
    "CVX", "XOM", "ABBV", "JNJ", "VZ", "MO", "PG", "DUK", "D", "PEP",
    # affordable, liquid chains (fit a £10k/pos pot)
    "KO", "T", "PFE", "F", "INTC", "BAC", "WFC", "CSCO", "MU", "GM",
    "SLB", "OXY", "KMI", "DVN", "GILD", "BMY", "CMCSA", "DOW", "WMB", "HPE",
    # mega-liquid chains — the deepest/tightest option markets there are. Their
    # strikes only fit a RAISED pot (TRADEPRO_WHEEL_PER_POSITION_GBP): the
    # notional gate decides affordability per the user's configured capital,
    # the universe just makes them CANDIDATES (config-driven, not pre-filtered).
    "NVDA", "GOOGL", "AAPL", "MSFT", "AMD", "QCOM",
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


def _fetch_nav_gbp() -> float | None:
    """Account NAV in GBP, best-effort, for the size-fit annotation (v1
    §F0.3-4: "contract notional vs NAV vs target allocation" — INFORMATIONAL
    only; per project_wheel_signal_vs_paper_capital_split candidates are
    NEVER capital-gated here, the risk engine's own notional cap already
    handles hard sizing). Reads the unified cross-broker account-state table
    (backend/TradePro.Api IntegrationsEndpoints, /integrations/account-state)
    and sums netLiquidation across accounts. IBKR reports in its account base
    currency (USD for the paper account this screen targets); converted to
    GBP with the same _FX_GBPUSD rate the rest of this file already uses for
    strike notional, so the two numbers are comparable. None on ANY failure
    — size-fit just doesn't render rather than showing a wrong number."""
    try:
        from . import push_to_api as _pta
        import requests
        base, tok = _pta.load_credentials()
        r = requests.get(f"{base}/api/integrations/account-state",
                          headers={"Authorization": f"Bearer {tok}"}, timeout=15)
        r.raise_for_status()
        accounts = (r.json() or {}).get("accounts") or []
        total_usd = sum(a.get("netLiquidation") or 0 for a in accounts)
        return round(total_usd / _FX_GBPUSD, 0) if total_usd else None
    except Exception as e:  # noqa: BLE001
        log.warning("NAV fetch failed (size-fit will be omitted): %s", e)
        return None


def _screen_symbol(ib, ib_insync, sym: str, cfg: OptionsRiskConfig, market_open: bool, nav_gbp: float | None = None) -> dict:
    """Build one candidate row: IV-Rank + regime + (live) chain → risk engine.

    `ib` may be None (Gateway unreachable) — IV-Rank then fails closed
    (available=False, visible reason) rather than attempting its own
    connection per symbol; regime and chain don't need it at all any more
    (bar-cache and G3 respectively), so the screen still produces real,
    honest candidates in that mode — just always BLOCKed on the IV-rank
    gate, which is the correct behaviour when that gate's input is
    genuinely unavailable, not a crash.
    """
    if ib is not None:
        ivr = fetch_iv_rank(sym, ib=ib)
    else:
        from ..quant_engine.options.iv_rank import IvRankResult
        ivr = IvRankResult(sym, available=False, reason="IBKR Gateway unreachable — IV-Rank needs its 52w OPTION_IMPLIED_VOLATILITY history, no non-Gateway source exists yet")

    # Daily closes for the regime (Ichimoku on the bar cache — yfinance-
    # backed, the same source get_market_state/the digest already use.
    # No Gateway needed: this used to require an ib_insync TRADES bars
    # request, which is exactly the session-contention dependency G3 was
    # built to remove from the chain fetch — removing it here too so
    # regime works even when no Gateway is reachable at all.
    regime = falling_knife = ref_close = None
    try:
        from datetime import timedelta
        from ..cache import ensure_cached
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=400)
        prices = ensure_cached("yahoo", sym, start, end)
        close_col = "adj_close" if "adj_close" in prices.columns else "close"
        closes = prices[close_col].dropna().tolist()
        regime, falling_knife = regime_from_closes(closes)
        if closes and closes[-1] > 0:
            ref_close = closes[-1]
    except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (no false positive)
        log.warning("%s regime fetch failed: %s", sym, e)

    # Options chain — G3 FIRST (TradePro's own API-hosted chain feed, real
    # broker delta/greeks/OI, OAuth REST session — no local Gateway needed),
    # then ib_insync/TWS IBKR (real model greeks too, but needs a reachable
    # local Gateway — the session-contention path G3 exists to avoid), then
    # yfinance as last resort (delayed/patchy; its IV is often missing → BS
    # delta ≈ 0 → nothing clears the band). Select the put nearest 0.27 delta
    # (BRD strike rule).
    oi = strike = delta = premium = notional_gbp = None
    spread = None
    dte = 35
    chain_ok = False
    chain_source = None
    spot_divergence = None  # |yf spot / IBKR close − 1| when both known
    from ..quant_engine.options.black_scholes import BlackScholesPricer
    from ..quant_engine.options.chains import fetch_chain, select_by_abs_delta, delta_of
    from ..quant_engine.options.chains_g3 import fetch_chain_g3
    from ..quant_engine.options.chains_ibkr import fetch_chain_ibkr
    pricer = BlackScholesPricer()

    # 0) G3 — TradePro's own chain feed (backend/TradePro.Api ChainEndpoints).
    try:
        gc = fetch_chain_g3(sym, target_dte=35, right="P")
        if gc and gc.puts and gc.spot > 0:
            t = max(gc.dte, 1) / 365.0
            q = select_by_abs_delta(gc.puts, 0.27, gc.spot, t, pricer)
            if q is not None:
                dte = gc.dte
                strike = q.strike
                delta = abs(delta_of(q, gc.spot, t, pricer))
                oi = q.open_interest
                spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                premium = q.mid if q.mid > 0 else None
                notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                chain_ok = True
                chain_source = "g3"
                if ref_close is None:
                    ref_close = gc.spot
    except Exception as e:  # noqa: BLE001 — fall through to ib_insync/yfinance
        log.warning("%s G3 chain failed, trying ib_insync: %s", sym, e)

    # 1) IBKR via ib_insync/TWS (authoritative, needs local Gateway). Reuse the
    # screen's connection + IBKR close as spot. Skipped when G3 already worked.
    if not chain_ok:
        try:
            ic = fetch_chain_ibkr(sym, target_dte=35, ib=ib, pricer=pricer, spot=ref_close)
            if ic and ic.puts and ic.spot > 0:
                t = max(ic.dte, 1) / 365.0
                q = select_by_abs_delta(ic.puts, 0.27, ic.spot, t, pricer)
                if q is not None:
                    dte = ic.dte
                    strike = q.strike
                    delta = abs(delta_of(q, ic.spot, t, pricer))
                    oi = q.open_interest
                    spread = q.spread if (q.bid > 0 and q.ask > 0) else None
                    premium = q.mid if q.mid > 0 else None
                    notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                    chain_ok = True
                    chain_source = "ibkr"
        except Exception as e:  # noqa: BLE001 — fall through to yfinance
            log.warning("%s IBKR chain failed, trying yfinance: %s", sym, e)

    # 2) yfinance fallback (only if IBKR gave nothing usable).
    if not chain_ok:
        try:
            chain = fetch_chain(sym, target_dte=35, pricer=pricer)
            # Spot-sanity guard: reject a chain whose spot disagrees with the
            # authoritative IBKR close by >15% (e.g. yfinance "$134" for INTC).
            spot_ok = True
            if chain and chain.spot > 0 and ref_close:
                spot_divergence = abs(chain.spot / ref_close - 1.0)
                if spot_divergence > 0.15:
                    spot_ok = False
                    log.warning("%s yf spot %.2f diverges %.0f%% from IBKR close %.2f — rejecting",
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
                    notional_gbp = round(strike * 100 / _FX_GBPUSD, 0)
                    chain_ok = True
                    chain_source = "yfinance"
        except Exception as e:  # noqa: BLE001 — None → risk BLOCKs (fail-visible)
            log.warning("%s yfinance chain fetch failed: %s", sym, e)

    earnings_in_window = _earnings_in_window(sym, dte)

    ctx = MarketContext(
        regime=Regime(regime) if regime else None,
        falling_knife=falling_knife,
        iv_rank=ivr.iv_rank if ivr.available else None,
        open_interest=oi, bid_ask_spread_usd=spread,
        premium_mid_usd=premium,   # scales the spread cap (relative, not $0.10 flat)
        earnings_in_expiry_window=earnings_in_window,
        # Screen on best-available data: a usable chain is enough to ASSESS
        # eligibility (execution still needs live quotes at the open).
        data_fresh=chain_ok,
        # IBKR chains are DELAYED until OPRA is enabled → the spread gate becomes
        # an advisory warning (candidates surface for paper, marked indicative)
        # instead of blocking on a stale wide spread. Flip to False once OPRA is
        # on (then real-time spreads hard-block again).
        quotes_delayed=(chain_source == "ibkr"),
    )
    cand = TradeCandidate(symbol=sym, structure=Structure.CASH_SECURED_PUT,
                          abs_delta=delta, dte=dte, strike=strike, notional_gbp=notional_gbp)
    decision = evaluate(cand, ctx, PortfolioState(), cfg)

    # Annualised premium yield — the income metric that ranks "best to trade".
    # premium ÷ strike (capital per share) scaled to a year by DTE. Only
    # meaningful with a real premium + strike (None pre-market → unranked).
    ann_yield_pct = None
    if premium and strike and strike > 0 and dte > 0:
        ann_yield_pct = round((premium / strike) * (365.0 / dte) * 100, 1)

    # Put-vs-buy side-by-side (v1 §F0.3-4) — the two ways to get long this
    # name. Selling the CSP: collect premium now; if assigned, effective cost
    # basis = strike - premium. If not assigned, the premium is pure income.
    # Only rendered with a real spot AND a real premium+strike — never a
    # fabricated comparison from a partial chain.
    put_vs_buy = None
    if ref_close and premium and strike:
        effective_cost = strike - premium
        put_vs_buy = {
            "buy_now_price": round(ref_close, 2),
            "sell_put_strike": strike,
            "sell_put_premium": round(premium, 2),
            "sell_put_effective_cost_if_assigned": round(effective_cost, 2),
            "discount_vs_buy_now_pct": round((ref_close - effective_cost) / ref_close * 100, 1),
        }

    # Size-fit — contract notional as a share of account NAV. Informational
    # only (see _fetch_nav_gbp docstring); None when NAV wasn't reachable.
    size_fit_pct = None
    if notional_gbp and nav_gbp:
        size_fit_pct = round(notional_gbp / nav_gbp * 100, 1)

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
        "dte": dte,
        "annualized_yield_pct": ann_yield_pct,
        "chain_source": chain_source,
        "ref_close": round(ref_close, 2) if ref_close else None,
        "spot_divergence_pct": round(spot_divergence * 100, 1) if spot_divergence is not None else None,
        "put_vs_buy": put_vs_buy,
        "size_fit_pct": size_fit_pct,
    }


def run_screen(symbols: list[str] | None = None) -> dict:
    import ib_insync
    from . import push_to_api as _pta
    from ..paper import market_hours

    universe = symbols or DEFAULT_UNIVERSE
    cfg = OptionsRiskConfig.from_env()   # capital sizing env-tunable (TRADEPRO_WHEEL_*)
    host = os.environ.get("TRADEPRO_IBKR_HOST", "127.0.0.1")
    port = int(os.environ.get("TRADEPRO_IBKR_PORT", "7500"))
    cid = int(os.environ.get("TRADEPRO_IBKR_DATA_CLIENT_ID", "97"))
    try:
        market_open = market_hours.is_open("us_equity", datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001
        market_open = False
    nav_gbp = _fetch_nav_gbp()

    # Gateway is now OPTIONAL: chain (G3) and regime (bar cache) no longer
    # need it — only IV-Rank still does (no non-Gateway source for 52w
    # OPTION_IMPLIED_VOLATILITY history exists yet). A degraded run without
    # Gateway still produces real regime + real chain data for every symbol,
    # just permanently BLOCKed on the IV-rank gate — honest, not a crash.
    ib = ib_insync.IB()
    connected = False
    try:
        ib.connect(host, port, clientId=cid, timeout=20)
        connected = True
    except Exception as e:  # noqa: BLE001
        log.warning("IBKR Gateway unreachable (%s) — running degraded: "
                    "chain via G3, regime via bar cache, IV-rank BLOCKED", e)

    rows = []
    try:
        for sym in universe:
            rows.append(_screen_symbol(ib if connected else None, ib_insync, sym, cfg, market_open, nav_gbp))
            log.info("screened %s", sym)
    finally:
        if connected:
            ib.disconnect()

    # Crown the single BEST eligible CSP: highest annualised yield, GREEN
    # preferred over YELLOW as a tiebreak. NEVER crown a non-eligible name —
    # if nothing clears every gate, best_symbol is None (no false "best").
    def _rank_key(r: dict) -> tuple:
        regime_rank = 1.0 if r.get("regime") == "GREEN" else 0.0
        return (r.get("annualized_yield_pct") or 0.0, regime_rank)

    eligible_rows = [r for r in rows if r.get("eligible")]
    best = max(eligible_rows, key=_rank_key) if eligible_rows else None
    for r in rows:
        r["is_best"] = bool(best and r["symbol"] == best["symbol"])

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "market_open": market_open,
        "gateway_available": connected,
        "candidates": rows,
        "best_symbol": best["symbol"] if best else None,
        "eligible_count": len(eligible_rows),
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
    if p.get("best_symbol"):
        b = next(c for c in p["candidates"] if c["symbol"] == p["best_symbol"])
        print(f"  ⭐ BEST: {b['symbol']} CSP ${b['suggested_strike']} — "
              f"{b['annualized_yield_pct']}% annualised, {b['regime']}")
    else:
        print("  ⭐ BEST: none — no candidate clears every gate right now")
    for c in p["candidates"]:
        mark = "⭐" if c.get("is_best") else ("✓" if c["eligible"] else "·")
        ivr = f"{c['iv_rank']:.0f}%" if c["iv_rank"] is not None else "n/a"
        yld = f"{c['annualized_yield_pct']:.0f}%/yr" if c.get("annualized_yield_pct") else ""
        print(f"  {mark} {c['symbol']:5} regime={c['regime'] or 'n/a':6} IV-Rank={ivr:>4} {yld:>7} "
              f"{'' if c['eligible'] else '— ' + '; '.join(c['blocks'][:2])}")
    return 0
