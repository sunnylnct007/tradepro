"""tradepro-wheel-backtest — wire the (previously unreachable) wheel backtester
to the bar cache and grade it against the PRE-REGISTERED gates.

Everything judged here was fixed in strategies/WHEEL_BACKTEST_GATES.md and
committed BEFORE the first run (owner directive 11 Aug 2026: thresholds set
before the output exists, QDB-style). This CLI:

  1. builds the universe by the registered rule — every DEFAULT_UNIVERSE
     symbol with ≥260 cached daily bars before the window's effective start;
  2. runs one independent $25k / 1-contract wheel per symbol, net of the
     registered costs (5% premium haircut + $1.50/leg commission);
  3. aggregates per-day equity into a PORTFOLIO curve (correlated drawdown +
     simultaneous-assignment pile-up — the 2020 question);
  4. prints the gate table PASS/FAIL plus the registered disclosures.

Windows: --window 2020 | 2022 | full (or --start/--end for exploration —
exploratory runs are NOT gate runs and are labeled as such).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import defaultdict

# Registered parameters (WHEEL_BACKTEST_GATES.md v1 / _V2.md) — change the
# file, not these.
SLICE_USD = 25_000.0
HAIRCUT = 0.05
COMMISSION = 1.50
OTM_PCT = 0.05
DTE = 30
WARMUP = 60
# v2 additions (WHEEL_BACKTEST_GATES_V2.md, committed cb51600 before any run)
V2_MIN_PREMIUM_USD = 0.20       # live OptionsRiskConfig default
V2_MIN_ANN_YIELD_PCT = 8.0      # live OptionsRiskConfig default
V2_IDLE_CASH_RATE = 0.04        # registered ON
WINDOWS = {
    "2020": ("2019-10-01", "2020-12-31", "2020-01-01"),
    "2022": ("2021-10-01", "2022-12-31", "2022-01-01"),
    "full": ("2019-10-01", None, "2020-01-01"),
}


def _load_closes(symbol: str):
    """(dates, closes) from the yahoo daily cache, adjusted series preferred."""
    from ..cache import load_cached
    df = load_cached("yahoo", symbol)
    if df is None or df.empty:
        return [], []
    col = "adj_close" if "adj_close" in df.columns else "close"
    ser = df[col].dropna()
    return [d.date() for d in ser.index], [float(v) for v in ser.to_list()]


def _regime_series(closes: list[float]) -> tuple[list[str | None], list[bool]]:
    """Per-bar (regime, falling_knife) from the SAME pure function the live
    screen calls — so the backtest's regime gate is the deployed rule, not a
    re-implementation. Bars before the function's 60-close minimum return
    None ⇒ the gate is inactive there (fail-open, never fabricated)."""
    from .options_screen import regime_from_closes
    regimes: list[str | None] = []
    knives: list[bool] = []
    for i in range(len(closes)):
        r, k = regime_from_closes(closes[: i + 1]) if i >= 60 else (None, None)
        regimes.append(r)
        knives.append(bool(k))
    return regimes, knives


_EARN_CACHE: dict[str, tuple[list, str]] = {}


def _earnings_dates(symbol: str, api_base: str | None) -> tuple[list[dt.date], str]:
    """Historical print dates for the earnings veto: the central STORE first
    (backfilled from Finnhub's bulk calendar — arbitrary ranges, permanent),
    yfinance as supplement. Returns (dates, source_label) so coverage can be
    disclosed per window rather than assumed."""
    if symbol in _EARN_CACHE:          # same dates for every window
        return _EARN_CACHE[symbol]
    out: set[dt.date] = set()
    src = []
    if api_base:
        try:
            import requests
            r = requests.get(f"{api_base.rstrip('/')}/api/earnings-calendar/{symbol}",
                             params={"back": 3600, "ahead": 400}, timeout=20)
            r.raise_for_status()
            rows = (r.json() or {}).get("events") or []
            for ev in rows:
                d = str(ev.get("report_date") or "")[:10]
                if d:
                    out.add(dt.date.fromisoformat(d))
            if rows:
                src.append(f"store:{len(rows)}")
        except Exception:  # noqa: BLE001 — store miss falls through to yfinance
            pass
    try:
        from ..earnings import fetch_earnings_in_range
        for h in fetch_earnings_in_range(symbol, lookback_days=2600):
            d = str(h.get("date") or "")[:10]
            if d:
                out.add(dt.date.fromisoformat(d))
        src.append("yfinance")
    except Exception:  # noqa: BLE001
        pass
    _EARN_CACHE[symbol] = (sorted(out), "+".join(src) or "none")
    return _EARN_CACHE[symbol]


def run_window(window: str, start: str, end: str | None, eff_start: str,
               *, symbols_override: list[str] | None = None, v2: bool = False) -> dict:
    from ..cli.options_screen import DEFAULT_UNIVERSE
    from ..quant_engine.options.wheel_backtest import simulate_wheel

    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end) if end else dt.date.today()
    eff_d = dt.date.fromisoformat(eff_start)

    api_base = None
    if v2:
        try:
            from .push_to_api import load_credentials
            api_base, _tok = load_credentials()
        except Exception:  # noqa: BLE001 — yfinance-only coverage then
            api_base = None

    per_symbol: dict[str, object] = {}
    skipped: list[str] = []
    earn_cov: dict[str, int] = {}
    for sym in (symbols_override or DEFAULT_UNIVERSE):
        dates, closes = _load_closes(sym)
        pre = sum(1 for d in dates if d < eff_d)
        if pre < 260:                      # registered coverage rule
            skipped.append(sym)
            continue
        idx = [i for i, d in enumerate(dates) if start_d <= d <= end_d]
        if len(idx) <= WARMUP + 10:
            skipped.append(sym)
            continue
        w_dates = [dates[i] for i in idx]
        w_closes = [closes[i] for i in idx]
        kwargs = {}
        if v2:
            regimes, knives = _regime_series(w_closes)
            e_dates, e_src = _earnings_dates(sym, api_base)
            in_window = [d for d in e_dates if w_dates[0] <= d <= w_dates[-1]]
            earn_cov[sym] = len(in_window)
            kwargs = dict(
                min_premium_usd=V2_MIN_PREMIUM_USD,
                min_ann_yield_pct=V2_MIN_ANN_YIELD_PCT,
                regime_by_day=regimes, knife_by_day=knives,
                earnings_dates=e_dates,
                # Veto active only where this symbol/window actually HAS
                # print dates — otherwise it would be an unmodelled gate
                # masquerading as a modelled one.
                earnings_modelled=bool(in_window),
                idle_cash_rate=V2_IDLE_CASH_RATE,
            )
        res = simulate_wheel(
            w_dates, w_closes, otm_pct=OTM_PCT, dte=DTE, contracts=1,
            start_capital=SLICE_USD, warmup=WARMUP,
            premium_haircut_pct=HAIRCUT, commission_per_leg=COMMISSION, **kwargs)
        res.symbol = sym
        per_symbol[sym] = res

    if not per_symbol:
        raise SystemExit(f"{window}: no symbols passed the coverage rule")

    # ── portfolio curve: per-date sum, forward-filling each symbol ──────
    all_dates = sorted({d for r in per_symbol.values() for d in r.curve_dates})
    port = []
    assigned_count = defaultdict(int)
    last_eq = {s: r.start_capital for s, r in per_symbol.items()}
    maps = {s: dict(zip(r.curve_dates, zip(r.equity_curve, r.state_by_day)))
            for s, r in per_symbol.items()}
    for d in all_dates:
        tot = 0.0
        for s in per_symbol:
            if d in maps[s]:
                eq, st = maps[s][d]
                last_eq[s] = eq
                if st in ("shares_pending", "covered_call"):
                    assigned_count[d] += 1
            tot += last_eq[s]
        port.append((d, tot))

    start_cap = sum(r.start_capital for r in per_symbol.values())
    final = port[-1][1]
    total_ret = (final / start_cap - 1) * 100
    peak, max_dd = -1e18, 0.0
    for _, v in port:
        peak = max(peak, v)
        max_dd = min(max_dd, (v / peak - 1) * 100)
    years = max((dt.date.fromisoformat(port[-1][0]) -
                 dt.date.fromisoformat(port[0][0])).days / 365.0, 1e-9)
    cagr = ((final / start_cap) ** (1 / years) - 1) * 100

    bh = sum(r.start_capital * (1 + r.buy_hold_return_pct / 100)
             for r in per_symbol.values())
    bh_ret = (bh / start_cap - 1) * 100
    worst_sym = min(per_symbol.values(), key=lambda r: r.max_drawdown_pct)
    util = sum(r.utilisation_pct for r in per_symbol.values()) / len(per_symbol)
    peak_assigned = max(assigned_count.values()) if assigned_count else 0
    prem = sum(r.premium_income for r in per_symbol.values())
    realised = sum(r.realised_pnl for r in per_symbol.values())
    costs = sum(r.costs_paid for r in per_symbol.values())
    assignments = sum(r.n_assignments for r in per_symbol.values())

    covered = sum(1 for v in earn_cov.values() if v > 0)
    return {
        "window": window, "n_symbols": len(per_symbol), "skipped": skipped,
        "v2": v2,
        "earnings_coverage": (f"{covered}/{len(per_symbol)} symbols have print dates in-window"
                              if v2 else "n/a (v1)"),
        "n_blocked_floor": sum(r.n_blocked_floor for r in per_symbol.values()),
        "n_blocked_regime": sum(r.n_blocked_regime for r in per_symbol.values()),
        "n_blocked_earnings": sum(r.n_blocked_earnings for r in per_symbol.values()),
        "n_g5_violations": sum(r.n_g5_violations for r in per_symbol.values()),
        "n_puts_sold": sum(r.n_puts_sold for r in per_symbol.values()),
        "start": port[0][0], "end": port[-1][0],
        "start_capital": start_cap, "final": round(final, 0),
        "total_return_pct": round(total_ret, 2), "cagr_pct": round(cagr, 2),
        "max_dd_pct": round(max_dd, 2), "buy_hold_return_pct": round(bh_ret, 2),
        "worst_symbol": worst_sym.symbol,
        "worst_symbol_dd_pct": round(worst_sym.max_drawdown_pct, 2),
        "utilisation_pct": round(util, 1), "peak_simultaneous_assigned": peak_assigned,
        "premium_income": round(prem, 0), "realised_share_pnl": round(realised, 0),
        "costs_paid": round(costs, 0), "n_assignments": assignments,
        "per_symbol": per_symbol,
    }


def _gate(label: str, ok: bool, detail: str) -> tuple[str, bool]:
    print(f"  {'PASS' if ok else 'FAIL'}  {label:6s} {detail}")
    return label, ok


def main() -> int:
    ap = argparse.ArgumentParser(description="Wheel backtest vs pre-registered gates")
    ap.add_argument("--window", choices=("2020", "2022", "full", "all"), default="all")
    ap.add_argument("--v2", action="store_true",
                    help="model the LIVE gates (premium floor, regime/knife, earnings veto, "
                         "idle cash) and grade vs WHEEL_BACKTEST_GATES_V2.md")
    ap.add_argument("--symbols", help="comma list override — EXPLORATORY, not a gate run")
    ap.add_argument("--details", action="store_true", help="per-symbol table")
    args = ap.parse_args()

    override = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
                if args.symbols else None)
    if override:
        print("⚠ EXPLORATORY RUN (symbol override) — not a gate run.\n")

    wanted = ["2020", "2022", "full"] if args.window == "all" else [args.window]
    results = {}
    for w in wanted:
        s, e, eff = WINDOWS[w]
        r = run_window(w, s, e, eff, symbols_override=override, v2=args.v2)
        results[w] = r
        print(f"\n── {w}: {r['start']} → {r['end']} · {r['n_symbols']} symbols "
              f"(skipped {len(r['skipped'])}: coverage rule) ──")
        print(f"  net return {r['total_return_pct']:+.2f}%  (buy-hold {r['buy_hold_return_pct']:+.2f}%)"
              f"  · CAGR {r['cagr_pct']:+.2f}%/yr  · max DD {r['max_dd_pct']:.2f}%")
        print(f"  premium ${r['premium_income']:,.0f} · realised share P&L ${r['realised_share_pnl']:,.0f}"
              f" · costs ${r['costs_paid']:,.0f} · assignments {r['n_assignments']}")
        print(f"  utilisation {r['utilisation_pct']}% · peak simultaneous assigned "
              f"{r['peak_simultaneous_assigned']}/{r['n_symbols']}"
              f" · worst name {r['worst_symbol']} DD {r['worst_symbol_dd_pct']:.1f}%")
        if r.get("v2"):
            print(f"  puts sold {r['n_puts_sold']} · blocked: floor {r['n_blocked_floor']}, "
                  f"regime {r['n_blocked_regime']}, earnings {r['n_blocked_earnings']}"
                  f" · earnings coverage {r['earnings_coverage']}"
                  f" · G5 violations {r['n_g5_violations']}")
        if args.details:
            for sym, res in sorted(r["per_symbol"].items(),
                                   key=lambda kv: kv[1].total_return_pct):
                print(f"    {sym:6s} ret {res.total_return_pct:+7.1f}% dd {res.max_drawdown_pct:6.1f}% "
                      f"puts {res.n_puts_sold:3d} assigned {res.n_assignments:2d} "
                      f"called-away {res.n_call_aways:2d} util {res.utilisation_pct:5.1f}%")

    if override:
        return 0

    gates_doc = ("WHEEL_BACKTEST_GATES_V2.md (cb51600)" if args.v2
                 else "WHEEL_BACKTEST_GATES.md (5817fe2)")
    print(f"\n══ GATES ({gates_doc}, committed BEFORE this run) ══")
    gates = []
    if "2022" in results:
        r = results["2022"]
        gates.append(_gate("G1a", r["total_return_pct"] >= -10,
                           f"2022 net {r['total_return_pct']:+.2f}% ≥ -10%"))
        edge = r["total_return_pct"] - r["buy_hold_return_pct"]
        gates.append(_gate("G1b", edge >= 8,
                           f"2022 edge vs buy-hold {edge:+.2f}pts ≥ +8"))
        gates.append(_gate("G1c", r["max_dd_pct"] >= -25,
                           f"2022 max DD {r['max_dd_pct']:.2f}% ≤ 25%"))
        gates.append(_gate("G4", r["worst_symbol_dd_pct"] >= -40,
                           f"2022 worst name {r['worst_symbol']} {r['worst_symbol_dd_pct']:.1f}% ≤ 40%"))
    if "2020" in results:
        r = results["2020"]
        gates.append(_gate("G2a", r["total_return_pct"] >= 0,
                           f"2020 net {r['total_return_pct']:+.2f}% ≥ 0%"))
        gates.append(_gate("G2b", r["max_dd_pct"] >= -30,
                           f"2020 max DD {r['max_dd_pct']:.2f}% ≤ 30%"))
        gates.append(_gate("G4", r["worst_symbol_dd_pct"] >= -40,
                           f"2020 worst name {r['worst_symbol']} {r['worst_symbol_dd_pct']:.1f}% ≤ 40%"))
    if "full" in results:
        r = results["full"]
        gates.append(_gate("G3", r["cagr_pct"] >= 8,
                           f"full-period NAV CAGR {r['cagr_pct']:+.2f}%/yr ≥ 8%"))
    if args.v2:
        # G5 CORRECTNESS: in the coverage era the earnings veto either works
        # or the run is lying about what it modelled.
        viol = sum(results[w]["n_g5_violations"] for w in results if w in ("2022", "full"))
        gates.append(_gate("G5", viol == 0,
                           f"CSPs opened across a known print: {viol} (must be 0)"))

    n_fail = sum(1 for _, ok in gates if not ok)
    print(f"\n  {'ALL GATES PASS — Phase 1 gate cleared' if n_fail == 0 else f'{n_fail} gate(s) FAILED — Phase 1 stays open'}")

    # Durable trace (owner 11 Aug: "need proper logs — what we have done and
    # how we are determining"): every GATE run leaves a central run_log row
    # with the code+gates GIT SHA, registered params, per-window headlines and
    # each gate's verdict — a backtest figure must never exist only in
    # terminal scrollback. (Full PG persistence of per-trade logs = migration
    # 063, next session.)
    try:
        import subprocess
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        sha = "unknown"
    try:
        from ..run_log import log_run
        headline = "; ".join(
            f"{w}: net {results[w]['total_return_pct']:+.2f}% dd {results[w]['max_dd_pct']:.1f}% "
            f"util {results[w]['utilisation_pct']}% ({results[w]['n_symbols']} syms)"
            for w in results)
        verdicts = ", ".join(f"{label}:{'PASS' if ok else 'FAIL'}" for label, ok in gates)
        log_run(
            "wheel-backtest", "gates",
            "ok" if n_fail == 0 else "fail",
            summary=(f"code {sha} vs gates {'V2(cb51600)' if args.v2 else 'v1(5817fe2)'} | "
                     f"slice ${SLICE_USD:,.0f} otm {OTM_PCT:.0%} dte {DTE} haircut {HAIRCUT:.0%} "
                     f"comm ${COMMISSION} | {headline} | {verdicts}"),
            error=None if n_fail == 0 else f"{n_fail} gate(s) failed — Phase 1 stays open",
        )
    except Exception:  # noqa: BLE001 — logging must never fail the run
        pass
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
