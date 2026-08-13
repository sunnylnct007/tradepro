"""tradepro-straddle-scan — the earnings straddle scanner (SPEC Part B §2).

OBSERVATIONAL ONLY: nothing this scanner emits is tradeable until Part B's
pre-registered gates clear on forward-collected data (§4). Its two jobs now:

  1. Compute the live edge read for every universe name with a CONFIRMED
     print inside the lookahead: implied move (ATM straddle mid / spot, at
     the nearest expiry AFTER the print) vs the name's own historical print
     behaviour (|close(T+1)/close(T-1) − 1| per past print, yfinance dates +
     our bar cache) — edge_ratio, full distribution, §2.4 gates.
  2. Persist every scan row + (via the G3 capture hook) the straddle's own
     quotes — the forward dataset that makes an honest backtest possible,
     since IBKR serves no history for expired contracts.

Expected outcome is pre-registered in the spec (§5): this is MORE likely to
show "no edge" than edge. A pass gets scrutinised, a fail is a result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import statistics
import sys

log = logging.getLogger("tradepro.straddle_scan")


def realized_print_moves(bar_dates: list[dt.date], closes: list[float],
                         print_dates: list[dt.date]) -> list[float]:
    """|close(T+1) / close(T-1) − 1| per historical print (spec §2.3 verbatim:
    the close BEFORE the announce date to the close AFTER it, catching both
    bmo and amc prints). Pure. Prints without both surrounding bars are
    skipped, never guessed."""
    idx = {d: i for i, d in enumerate(bar_dates)}
    out: list[float] = []
    for pd_ in print_dates:
        i = idx.get(pd_)
        if i is None:
            later = [j for j, d in enumerate(bar_dates) if d >= pd_]
            if not later:
                continue
            i = later[0]
        if i - 1 < 0 or i + 1 >= len(closes):
            continue
        prev_c, next_c = closes[i - 1], closes[i + 1]
        if prev_c and prev_c > 0 and next_c and next_c > 0:
            out.append(abs(next_c / prev_c - 1.0) * 100.0)
    return out


def straddle_gates(*, edge_ratio: float | None, n_prints: int,
                   iv_pctile: float | None, iv_hv: float | None,
                   worst_leg_spread_pct: float | None, min_leg_oi: int | None,
                   cost_pct_of_nav: float | None) -> dict:
    """SPEC §2.4 gates — each returns value + verdict; candidate only when
    every DECIDABLE gate passes and none of the required ones is unknown.
    IV percentile is None while our iv-daily store matures: that gate then
    reads 'unknown' and BLOCKS candidacy (can't-verify ≠ pass)."""
    gates = {
        "edge_ratio_gt_1_15": {"value": edge_ratio, "pass": bool(edge_ratio and edge_ratio > 1.15)},
        "sample_ge_8": {"value": n_prints, "pass": n_prints >= 8},
        "iv_pctile_lt_50": {"value": iv_pctile,
                            "pass": None if iv_pctile is None else iv_pctile < 50},
        "iv_hv_lt_1": {"value": iv_hv, "pass": None if iv_hv is None else iv_hv < 1.0},
        "leg_spread_le_8pct": {"value": worst_leg_spread_pct,
                               "pass": None if worst_leg_spread_pct is None
                               else worst_leg_spread_pct <= 8.0},
        "leg_oi_ge_500": {"value": min_leg_oi,
                          "pass": None if min_leg_oi is None else min_leg_oi >= 500},
        "cost_le_1_5pct_nav": {"value": cost_pct_of_nav,
                               "pass": None if cost_pct_of_nav is None
                               else cost_pct_of_nav <= 1.5},
    }
    candidate = all(g["pass"] is True for g in gates.values())
    return {"gates": gates, "candidate": candidate}


def _scan_symbol(sym: str, report_date: dt.date, base: str, token: str) -> dict | None:
    from ..cache import load_cached
    from ..earnings import fetch_earnings_in_range
    from ..quant_engine.options.chains_g3 import fetch_chain_g3
    from ..quant_engine.options.iv_rank import fetch_iv_rank_web

    # Nearest expiry AFTER the print (spec §2.6 default; the greeks dial).
    probe = fetch_chain_g3(sym, target_dte=max((report_date - dt.date.today()).days + 3, 3),
                           right="P")
    if probe is None:
        return None
    after = sorted(e for e in (probe.available_expiries or [])
                   if dt.date.fromisoformat(e) >= report_date)
    if not after:
        return None
    expiry = after[0]
    chain = fetch_chain_g3(sym, expiry=expiry, right=None)   # both legs
    if chain is None or not chain.puts or not chain.calls or chain.spot <= 0:
        return None
    spot = chain.spot
    atm_put = min(chain.puts, key=lambda q: abs(q.strike - spot))
    atm_call = min((c for c in chain.calls if c.strike == atm_put.strike),
                   key=lambda q: abs(q.strike - spot), default=None)
    if atm_call is None or atm_put.mid <= 0 or atm_call.mid <= 0:
        return None
    straddle_mid = atm_put.mid + atm_call.mid
    implied_move_pct = straddle_mid / spot * 100.0

    # Historical prints → realized moves (bar cache + yfinance print dates).
    hist = fetch_earnings_in_range(sym, lookback_days=1100)   # ~3y ≈ 12 prints
    print_dates = [dt.date.fromisoformat(str(h["date"])[:10]) for h in hist if h.get("date")]
    bars = load_cached("yahoo", sym)
    col = "adj_close" if "adj_close" in bars.columns else "close"
    ser = bars[col].dropna()
    moves = realized_print_moves([d.date() for d in ser.index],
                                 [float(v) for v in ser.to_list()], print_dates)
    moves = moves[-12:]
    med = statistics.median(moves) if moves else None
    p25 = statistics.quantiles(moves, n=4)[0] if len(moves) >= 4 else None
    p75 = statistics.quantiles(moves, n=4)[2] if len(moves) >= 4 else None
    edge = (med / implied_move_pct) if (med and implied_move_pct > 0) else None

    ivr = fetch_iv_rank_web(sym, api_base=base, api_token=token)
    def _spread_pct(q):
        return (q.spread / q.mid * 100.0) if (q.bid > 0 and q.ask > 0 and q.mid > 0) else None
    spreads = [s for s in (_spread_pct(atm_put), _spread_pct(atm_call)) if s is not None]
    ois = [o for o in (atm_put.open_interest, atm_call.open_interest) if o is not None]
    g = straddle_gates(
        edge_ratio=edge, n_prints=len(moves),
        iv_pctile=(ivr.iv_rank if ivr.available else None),   # honest: rank once mature, else None
        iv_hv=(ivr.iv_hv_ratio if ivr.available else None),
        worst_leg_spread_pct=max(spreads) if len(spreads) == 2 else None,
        min_leg_oi=min(ois) if len(ois) == 2 else None,
        cost_pct_of_nav=None,   # wired when PortfolioState lands; unknown blocks candidacy
    )
    return {
        "symbol": sym, "reportDate": report_date.isoformat(), "expiry": expiry,
        "spot": round(spot, 2), "straddleMid": round(straddle_mid, 2),
        "impliedMovePct": round(implied_move_pct, 2),
        "realizedMedianPct": round(med, 2) if med is not None else None,
        "realizedP25Pct": round(p25, 2) if p25 is not None else None,
        "realizedP75Pct": round(p75, 2) if p75 is not None else None,
        "nPrints": len(moves),
        "edgeRatio": round(edge, 3) if edge is not None else None,
        "ivHvRatio": ivr.iv_hv_ratio if ivr.available else None,
        "ivPctile": ivr.iv_rank if ivr.available else None,
        "perLegOiMin": min(ois) if len(ois) == 2 else None,
        "perLegSpreadPctMax": round(max(spreads), 2) if len(spreads) == 2 else None,
        "candidate": g["candidate"],
        "gatesJson": json.dumps(g["gates"]),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Earnings straddle scanner (observational)")
    ap.add_argument("--lookahead-days", type=int, default=15)
    ap.add_argument("--symbols", help="comma override (default: wheel universe)")
    args = ap.parse_args()

    from datetime import datetime, timezone
    from .options_screen import DEFAULT_UNIVERSE, _ETF_UNDERLYINGS, _next_confirmed_earnings
    from .push_to_api import load_credentials
    from ..run_log import log_run
    import requests

    base, token = load_credentials()
    started = datetime.now(timezone.utc)
    universe = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
                if args.symbols else [s for s in DEFAULT_UNIVERSE if s not in _ETF_UNDERLYINGS])
    horizon = dt.date.today() + dt.timedelta(days=args.lookahead_days)

    rows, skipped = [], 0
    for sym in universe:
        e_date, answered = _next_confirmed_earnings(sym)
        if e_date is None or e_date > horizon:
            skipped += 1
            continue
        try:
            row = _scan_symbol(sym, e_date, base, token)
        except Exception as e:  # noqa: BLE001 — one bad name never kills the sweep
            log.warning("%s scan failed: %s", sym, e)
            row = None
        if row:
            rows.append(row)
            log.info("%s: print %s · implied %.1f%% · realized med %s%% (n=%s) · edge %s · candidate=%s",
                     sym, row["reportDate"], row["impliedMovePct"],
                     row["realizedMedianPct"], row["nPrints"], row["edgeRatio"], row["candidate"])

    if rows:
        r = requests.post(f"{base.rstrip('/')}/api/options/straddle-scan",
                          json={"rows": rows},
                          headers={"Authorization": f"Bearer {token}"} if token else {},
                          timeout=30)
        r.raise_for_status()
    n_cand = sum(1 for r_ in rows if r_["candidate"])
    summary = (f"{len(rows)} names with confirmed prints ≤{args.lookahead_days}d scanned, "
               f"{n_cand} candidate(s); {skipped} without near prints")
    log_run("straddle-scan", "scan", "ok" if rows or skipped else "warn",
            summary=summary, started=started, base=base, token=token)
    print(f"straddle-scan: {summary}")
    for r_ in sorted(rows, key=lambda x: -(x["edgeRatio"] or 0)):
        print(f"  {r_['symbol']:6s} print {r_['reportDate']} · implied {r_['impliedMovePct']:.1f}% "
              f"· realized med {r_['realizedMedianPct']}% (n={r_['nPrints']}) "
              f"· edge {r_['edgeRatio']} · candidate={r_['candidate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
