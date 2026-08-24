"""Resting limit at the trigger level — graded against RESTING_LIMIT_GATES_V1.md
(committed 4f1d876 BEFORE this run).

The owner asked why he cannot rest an order at the 2.5-sigma level rather than
waiting for a settled close. Exploratory says the limit gets 3x the trades at a
slightly better per-trade return, and breaks G5 with a -28.1% worst trade. This
tests whether the tail can be brought back inside the gates.
"""
from __future__ import annotations
import statistics as st
import numpy as np
from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals.mean_reversion import (
    SIGMA, BB_WINDOW, TREND_WINDOW, MAX_HOLD, STOP_PCT)

MAXDAY = 0.35


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


DATA = {}
for _s in universe_symbols():
    _df = _load(_s)
    if _df is None or "open" not in _df.columns:
        continue
    _c = _df["close"].tolist()
    _v = _df["volume"].tolist() if "volume" in _df.columns else None
    if not poison_check(_c, _v)[0]:
        continue
    DATA[_s] = (_c, _df["high"].tolist(), _df["low"].tolist(), _df["open"].tolist(),
                [str(x)[:10] for x in _df.index])


def variant(mode, stop_pct=STOP_PCT):
    """Each variant gets its OWN pass over every bar — the first exploratory
    attempt ran two arms in one loop and they consumed each other's bars,
    which showed up as the control posting 543 trades against its true 2,501."""
    res, dates, sidx = [], [], []
    for si, (c, h, l, o, d) in enumerate(DATA.values()):
        n = len(c)
        i = 210
        while i < n - 2:
            w = c[i - BB_WINDOW + 1:i + 1]
            sd = st.pstdev(w)
            if sd <= 0 or c[i] <= sma(c, i, TREND_WINDOW):
                i += 1; continue
            lvl = (sum(w) / BB_WINDOW) - SIGMA * sd
            if mode == "control":
                if not (c[i] < lvl):
                    i += 1; continue
                entry = o[i + 1]
            else:
                if not (l[i + 1] <= lvl <= h[i + 1]):
                    i += 1; continue
                if mode == "trend_recheck" and c[i + 1] <= sma(c, i + 1, TREND_WINDOW):
                    i += 1; continue
                entry = lvl
            # D: keep the early fill, but bail at the next open if the session
            # did NOT also close below the level — that fill was only a wick,
            # and the wicks are what produce the tail.
            if mode == "close_confirm" and c[i + 1] >= lvl:
                res.append(100 * (o[i + 2] / entry - 1) if i + 2 < n else 0.0)
                dates.append(d[i]); sidx.append(si); i += 2; continue
            stop = entry * (1 - stop_pct)
            r = None; j = i + 1
            for j in range(i + 1, min(n - 1, i + 1 + MAX_HOLD) + 1):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAXDAY:
                    break
                tgt = sma(c, j, BB_WINDOW)
                if l[j] <= stop:
                    r = 100 * (min(stop, o[j]) / entry - 1); break
                if h[j] >= tgt:
                    r = 100 * (max(tgt, o[j]) / entry - 1); break
            if r is None:
                j = min(n - 1, i + 1 + MAX_HOLD); r = 100 * (c[j] / entry - 1)
            res.append(r); dates.append(d[i]); sidx.append(si); i = j + 1
    return np.array(res), np.array(dates), np.array(sidx)


VARIANTS = [("A control (the rule)", "control", STOP_PCT),
            ("B naive limit", "limit", STOP_PCT),
            ("C limit + 6% stop", "limit", 0.06),
            ("D limit + close confirm", "close_confirm", STOP_PCT),
            ("E limit + trend recheck", "trend_recheck", STOP_PCT)]

out = {}
print(f"{'variant':<26}{'trades':>8}{'win%':>7}{'mean%':>8}{'worst%':>9}{'tail%':>7}{'hold':>6}")
for label, mode, sp in VARIANTS:
    a, d, s = variant(mode, sp)
    wins = a[a > 0]
    k = max(1, len(a) // 100)
    tail = 100 * np.sort(a)[::-1][:k].sum() / a.sum() if a.sum() > 0 else 999
    out[label] = (a, d, s, tail)
    print(f"{label:<26}{len(a):>8}{100*(a>0).mean():>6.1f}%{a.mean():>7.2f}%"
          f"{a.min():>8.1f}%{tail:>6.1f}%{MAX_HOLD:>6}")

ctrl_n, ctrl_m = len(out["A control (the rule)"][0]), out["A control (the rule)"][0].mean()
print(f"\nGATES  (control: {ctrl_n} trades, {ctrl_m:+.2f}%/trade)\n")
for label, _, _ in VARIANTS:
    if label.startswith("A"):
        continue
    a, d, s, tail = out[label]
    di = np.array([int(x[:4]) * 10000 + int(x[5:7]) * 100 + int(x[8:10]) for x in d])
    mid = np.median(di)
    cells = {"time 1st": di < mid, "time 2nd": di >= mid,
             "sym even": s % 2 == 0, "sym odd": s % 2 == 1}
    r6 = all(a[m].mean() >= ctrl_m and a[m].min() >= -25 for m in cells.values())
    g = {"R1 trades": len(a) >= ctrl_n, "R2 mean": a.mean() >= ctrl_m,
         "R3 worst>=-25": a.min() >= -25, "R4 tail<=25": tail <= 25, "R6 splits": r6}
    bad = [k for k, v in g.items() if not v]
    print(f"  {label:<26}{'ALL PASS' if not bad else 'FAIL — ' + ', '.join(bad)}")
    if not bad:
        for cn, m in cells.items():
            print(f"      {cn}: {a[m].mean():+.2f}%  worst {a[m].min():.1f}%  n={int(m.sum())}")
