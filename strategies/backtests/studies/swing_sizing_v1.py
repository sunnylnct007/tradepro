"""SWING_SIZING_GATES_V1 — does the live position size survive the real tail?

Gates frozen in SWING_SIZING_GATES_V1.md before this ran.

A date-ordered REPLAY, not a Monte Carlo. Mean-reversion signals arrive in
clusters — a market two sigma below its mean is two sigma below it in many
names at once — and resampling trades independently would spread 2008 evenly
across twenty years and report a drawdown the strategy cannot have. The desk
has recorded that trap before: "Monte Carlo on gated trades CANNOT see a
crash." This walks the calendar and applies the live constraints as the
daemon applies them.
"""
import datetime as dt
import statistics as st
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
from backtests.studies.swing_out_of_sample_v1 import _load_many, _sma  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as W, MAX_HOLD, SIGMA, STOP_PCT, TREND_WINDOW)
from tradepro_strategies.universe import universe_symbols  # noqa: E402

MAX_DAY_MOVE = 0.35
# The live daemon's own arguments, read off the running plist on 20 Sep:
#   --capital-usd 150000 --max-open-positions 15 --max-position-pct-of-capital 5
LIVE_CAPITAL = 150_000.0
LIVE_MAX_OPEN = 15
LIVE_PCT = 0.05
SWEEP = (0.02, 0.03, 0.05, 0.08, 0.10)

G1_MAX_DD, G2_MAX_DAY, G4_MAX_EXPOSURE = 25.0, 10.0, 80.0


def build_signals(data: dict) -> dict:
    """Every signal the live rule fires, keyed by date. Non-overlapping per
    symbol, exactly as the rule holds one position per name."""
    by_date: dict[str, list] = defaultdict(list)
    for sym, (c, h, l, o, d) in sorted(data.items()):
        n = len(c)
        i = 210
        while i < n - 1:
            m = _sma(c, i, W)
            sd = st.pstdev(c[i - W + 1:i + 1])
            if not (sd > 0 and c[i] > _sma(c, i, TREND_WINDOW)):
                i += 1
                continue
            if (c[i] - m) / sd > -SIGMA:
                i += 1
                continue
            entry, stop = c[i], c[i] * (1 - STOP_PCT)
            out = None
            for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    out = "corrupt"
                    break
                tgt = _sma(c, j, W)
                fs, ft = min(stop, o[j]), max(tgt, o[j])
                if l[j] <= stop:
                    out = (100 * (fs / entry - 1), j - i)
                    break
                if h[j] >= tgt:
                    out = (100 * (ft / entry - 1), j - i)
                    break
            if out == "corrupt":
                i = j + 1
                continue
            if out is None:
                j = min(n - 1, i + MAX_HOLD)
                out = (100 * (c[j] / entry - 1), j - i)
            by_date[d[i]].append({"sym": sym, "ret": out[0], "days": out[1],
                                  "exit": d[min(n - 1, i + out[1])]})
            i += max(1, out[1]) + 1
    return by_date


def replay(by_date: dict, pct: float, max_open: int, capital: float):
    """Compound the sleeve through the calendar under the live constraints."""
    dates = sorted(by_date)
    all_days = sorted({d for d in dates}
                      | {t["exit"] for v in by_date.values() for t in v})
    equity = capital
    open_pos: list[dict] = []          # {exit, size, ret}
    curve, peak, max_dd = [], capital, 0.0
    worst_day, peak_expo = 0.0, 0.0

    for day in all_days:
        # 1. close anything due today, realising P&L
        still = []
        realised = 0.0
        for p in open_pos:
            if p["exit"] <= day:
                realised += p["size"] * p["ret"] / 100.0
            else:
                still.append(p)
        open_pos = still
        before = equity
        equity += realised

        # 2. open today's signals, newest first, up to the cap
        for t in by_date.get(day, []):
            if len(open_pos) >= max_open:
                break
            size = equity * pct
            open_pos.append({"exit": t["exit"], "size": size, "ret": t["ret"]})

        expo = 100 * sum(p["size"] for p in open_pos) / equity if equity > 0 else 0
        peak_expo = max(peak_expo, expo)
        if before > 0:
            worst_day = min(worst_day, 100 * (equity / before - 1))
        peak = max(peak, equity)
        max_dd = max(max_dd, 100 * (1 - equity / peak))
        curve.append((day, equity))
        if equity <= 0:
            break

    return {"final": equity, "ret_pct": 100 * (equity / capital - 1),
            "max_dd": max_dd, "worst_day": worst_day,
            "peak_expo": peak_expo, "curve": curve}


def main() -> int:
    syms = sorted({s.upper() for s in universe_symbols(strict=False)})
    data = _load_many(syms, "traded")
    by_date = build_signals(data)
    n_sig = sum(len(v) for v in by_date.values())
    dates = sorted(by_date)
    print(f"\n{n_sig} signals across {len(dates)} dates  "
          f"({dates[0]} → {dates[-1]})")

    # G0: did the sleeve actually trade the crises?
    def _in(lo, hi):
        return sum(len(by_date[d]) for d in dates if lo <= d <= hi)
    n08, n20 = _in("2008-01-01", "2009-06-30"), _in("2020-02-01", "2020-05-31")
    print(f"  signals in the 2008 crisis: {n08}   in the 2020 crash: {n20}")
    g0 = n08 > 0 and n20 > 0

    print(f"\n{'size':>6}{'final':>14}{'return':>11}{'max DD':>9}"
          f"{'worst day':>11}{'peak expo':>11}")
    results = {}
    for pct in SWEEP:
        r = replay(by_date, pct, LIVE_MAX_OPEN, LIVE_CAPITAL)
        results[pct] = r
        tag = "  ← LIVE" if pct == LIVE_PCT else ""
        print(f"{pct:>5.0%}{r['final']:>14,.0f}{r['ret_pct']:>+10.0f}%"
              f"{r['max_dd']:>8.1f}%{r['worst_day']:>10.1f}%"
              f"{r['peak_expo']:>10.0f}%{tag}")

    live = results[LIVE_PCT]
    print("\n— gates (frozen in SWING_SIZING_GATES_V1.md), judged at the LIVE 5% —")
    checks = (
        ("G0 traded both crises", g0, f"2008 n={n08}, 2020 n={n20}"),
        (f"G1 max DD <= {G1_MAX_DD}%", live["max_dd"] <= G1_MAX_DD,
         f"{live['max_dd']:.1f}%"),
        (f"G2 worst day <= {G2_MAX_DAY}%", abs(live["worst_day"]) <= G2_MAX_DAY,
         f"{live['worst_day']:.1f}%"),
        ("G3 return positive", live["ret_pct"] > 0, f"{live['ret_pct']:+.0f}%"),
        (f"G4 peak exposure <= {G4_MAX_EXPOSURE}%",
         live["peak_expo"] <= G4_MAX_EXPOSURE, f"{live['peak_expo']:.0f}%"),
    )
    for label, ok, got in checks:
        print(f"  {label:26s} {'PASS' if ok else 'FAIL'}  ({got})")

    if not all(ok for _, ok, _ in checks):
        passing = [p for p in SWEEP if results[p]["max_dd"] <= G1_MAX_DD
                   and abs(results[p]["worst_day"]) <= G2_MAX_DAY]
        if passing:
            best = max(passing)
            b = results[best]
            print(f"\nLargest size clearing the drawdown bar: {best:.0%} "
                  f"(DD {b['max_dd']:.1f}%, return {b['ret_pct']:+.0f}%)")
            print(f"  cost of moving {LIVE_PCT:.0%} → {best:.0%}: "
                  f"{b['ret_pct'] - live['ret_pct']:+.0f}pp of total return, "
                  f"{live['max_dd'] - b['max_dd']:+.1f}pp less drawdown")
        else:
            print("\nNO swept size clears the drawdown bar — the drawdown is "
                  "structural, not a sizing problem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
