"""Entering at 15:50 instead of the next open — does the gain survive the fill?

Gates and predictions pre-registered in EARLY_ENTRY_GATES_V1.md (97a08f1)
BEFORE this ran.

The prize: 69% of daily return accrues overnight, and entry at the signal close
is +0.854%/trade against +0.769% at the next open. The 15:50 signal already
matches the settled answer 99.81% of the time with zero false entries.

The question here is different and unmeasured: filling at 15:50 is NOT filling
at the close. real gain = overnight drift - (close - 15:50 price).
"""
from __future__ import annotations

import glob
import statistics as st
from collections import defaultdict

import pandas as pd

from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals import mean_reversion as mr
from tradepro_strategies.universe import poison_check, universe_symbols

BASE = "/Users/skumar/.tradepro/bar_cache/us_etf"
CUT_H, CUT_M = 19, 50          # 15:50 New York = 19:50 UTC


def price_at_cutoff(sym: str) -> dict[str, float]:
    """Last 5-minute close at or before 19:50 UTC, per session."""
    files = sorted(glob.glob(f"{BASE}/{sym}/5m/*.parquet"))
    if not files:
        return {}
    try:
        d = pd.concat([pd.read_parquet(f) for f in files]).sort_index()
    except Exception:  # noqa: BLE001
        return {}
    idx = d.index.tz_convert("UTC") if d.index.tz else d.index
    mins = idx.hour * 60 + idx.minute
    keep = d[(mins <= CUT_H * 60 + CUT_M) & (mins >= 13 * 60 + 30)]
    if keep.empty:
        return {}
    out: dict[str, float] = {}
    for ts, close in zip(keep.index, keep["close"]):
        out[str(ts)[:10]] = float(close)      # later bars overwrite earlier
    return out


def sma(x, i, n):
    return sum(x[i - n + 1:i + 1]) / n


def run():
    rows = []
    for si, sym in enumerate(universe_symbols()):
        cut = price_at_cutoff(sym)
        if len(cut) < 150:                     # needs real 5m history
            continue
        d = _load(sym)
        if d is None or len(d) < 300:
            continue
        c = d["close"].tolist(); h = d["high"].tolist()
        l = d["low"].tolist(); o = d["open"].tolist()
        dt = [str(x)[:10] for x in d.index]
        if not poison_check(c, d["volume"].tolist() if "volume" in d else None)[0]:
            continue
        i = 210
        while i < len(c) - 1:
            if not mr.entry_signal(c, i):
                i += 1; continue
            if dt[i] not in cut:               # no intraday bar that day
                i += 1; continue
            entries = {"A_next_open": o[i + 1], "B_1550": cut[dt[i]], "C_close": c[i]}
            res = {}
            for label, entry in entries.items():
                if entry <= 0:
                    res = {}; break
                stop = entry * (1 - mr.STOP_PCT)
                # A enters NEXT session, so its holding window starts a day later.
                start = i + 2 if label == "A_next_open" else i + 1
                out = None
                for j in range(start, min(len(c), i + mr.MAX_HOLD + 1)):
                    t = sma(c, j, mr.BB_WINDOW)
                    if l[j] <= stop:
                        out = 100 * (min(stop, o[j]) / entry - 1); break
                    if h[j] >= t:
                        out = 100 * (max(t, o[j]) / entry - 1); break
                if out is None:
                    j = min(len(c) - 1, i + mr.MAX_HOLD)
                    out = 100 * (c[j] / entry - 1)
                res[label] = out
            if len(res) == 3:
                rows.append({"sym": sym, "si": si, "date": dt[i], **res})
            i += 1
    return rows


def summarise(rows, label):
    if not rows:
        print(f"{label:<18} no trades"); return None
    a = [r["A_next_open"] for r in rows]
    b = [r["B_1550"] for r in rows]
    c = [r["C_close"] for r in rows]
    print(f"{label:<18}{len(rows):>7}"
          f"{st.mean(a):>10.3f}%{st.mean(b):>10.3f}%{st.mean(c):>10.3f}%"
          f"{st.mean(b) - st.mean(a):>+11.3f}%")
    return st.mean(b) - st.mean(a)


if __name__ == "__main__":
    rows = run()
    syms = {r["sym"] for r in rows}
    print(f"{len(rows)} signals across {len(syms)} symbols with >=150 sessions of 5m history\n")
    print(f"{'cell':<18}{'n':>7}{'A open':>11}{'B 15:50':>11}{'C close':>11}{'B - A':>12}")
    full = summarise(rows, "FULL SAMPLE")
    mid = sorted(r["date"] for r in rows)[len(rows) // 2]
    print()
    cells = {
        "time 1st half": [r for r in rows if r["date"] < mid],
        "time 2nd half": [r for r in rows if r["date"] >= mid],
        "symbols even": [r for r in rows if r["si"] % 2 == 0],
        "symbols odd": [r for r in rows if r["si"] % 2 == 1],
    }
    deltas = {k: summarise(v, k) for k, v in cells.items()}
    worst_a = min(r["A_next_open"] for r in rows)
    worst_b = min(r["B_1550"] for r in rows)
    print(f"\nworst trade   A {worst_a:.1f}%   B {worst_b:.1f}%   (E4 allows B to be 2pt worse)")
    print("\nGATES")
    print(f"  E1 B beats A on mean          {'PASS' if full and full > 0 else 'FAIL'}  ({full:+.3f}%)")
    for k in ("time 1st half", "time 2nd half"):
        print(f"  E2 {k:<26}{'PASS' if deltas[k] and deltas[k] > 0 else 'FAIL'}  ({deltas[k]:+.3f}%)")
    for k in ("symbols even", "symbols odd"):
        print(f"  E3 {k:<26}{'PASS' if deltas[k] and deltas[k] > 0 else 'FAIL'}  ({deltas[k]:+.3f}%)")
    print(f"  E4 worst trade within 2pt     {'PASS' if worst_b >= worst_a - 2 else 'FAIL'}")

# ── RESULT, 26 Aug 2026 ────────────────────────────────────────────────────
#
# REJECTED. All four gates fail, and B does not merely gain LESS than hoped —
# it LOSES to A by 1.52 points per trade.
#
#   cell                n     A open    B 15:50    C close      B - A
#   FULL SAMPLE        76     3.915%     2.394%     2.976%     -1.521%
#   time 1st half      38     4.532%     1.303%     1.950%     -3.229%
#   time 2nd half      38     3.297%     3.484%     4.003%     +0.187%
#   symbols even       40     4.005%     1.462%     2.429%     -2.543%
#   symbols odd        36     3.814%     3.429%     3.584%     -0.385%
#
#   worst trade   A -8.0%   B -17.3%
#
# MY PREDICTION WAS WRONG IN DIRECTION, NOT JUST MAGNITUDE. I predicted B would
# beat A by roughly half the 0.085% close-vs-open gap. It loses by 1.5 points.
#
# THE MECHANISM, AND IT INVERTS THE PREMISE. The 0.085% figure comes from the
# overnight drift measured across ALL stock-days: +0.055% close→open on average.
# This rule does not enter on an average day. It enters after a **2.5-sigma
# fall**, and a name that has just dropped that hard is not a random draw from
# the overnight distribution — it is a falling knife, and the fall frequently
# continues overnight.
#
# So entering at 15:50 buys the overnight move that entering at the next open
# AVOIDS. The next open is not a cost paid for waiting; on these entries it is
# a discount for waiting. The whole premise of EARLY_ENTRY_CANDIDATE.md —
# "69% of the daily return accrues overnight, so entering at the next open
# hands it over" — applies a population-wide statistic to a deliberately
# non-representative subpopulation.
#
# That is the same error shape as the volume ratio and the seam detector this
# week: a number that is true of the whole is applied to a part chosen for
# being unlike the whole.
#
# WHAT THIS RESULT IS NOT. n=76 across 25 symbols, and the mean returns here
# (A at +3.9%) are three times the strategy's own +1.1%, so this sample is not
# representative of the rule either. **A rejection on 76 trades is weak
# evidence.** What it is NOT is evidence of a gain: nothing here supports
# entering early, and the point estimate is strongly negative.
#
# Recorded as REJECTED with the sample limit attached. Worth re-running when
# the 5-minute backfill covers more than 25 names — but the mechanism above is
# a reason to expect the answer to hold, not a reason to hope it flips.
