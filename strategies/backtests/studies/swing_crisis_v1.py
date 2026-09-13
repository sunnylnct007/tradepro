"""SWING_CRISIS_GATES_V1 — does the edge survive 2008?

Runs the live swing rule unchanged over the extended store and splits trades
by era: POST-2010 (the sample every published figure was measured on) and
CRISIS 2007-01-01..2009-12-31 (newly visible after the backfill).

Gates C0-C4 and the prediction were frozen in SWING_CRISIS_GATES_V1.md BEFORE
the backfill completed. Nothing here may be tuned after seeing the numbers.

Harness identical to mean_reversion_v2: fill-at-open stops, both-hit assumes
worst, corrupt-bar skip, timeout close at MAX_HOLD.
"""
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from tradepro_strategies.universe import universe_symbols, poison_check  # noqa: E402
from tradepro_strategies.cli.build_universe import _load  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    SIGMA, BB_WINDOW as W, TREND_WINDOW, STOP_PCT, MAX_HOLD)

MAX_DAY_MOVE = 0.35
CRISIS = ("2007-01-01", "2009-12-31")


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def simulate(c, h, l, o, d, sym, rows):
    n = len(c)
    i = TREND_WINDOW + W
    while i < n - 1:
        m = _sma(c, i, W)
        sd = st.pstdev(c[i - W + 1:i + 1])
        if not (sd > 0 and c[i] > _sma(c, i, TREND_WINDOW)
                and (c[i] - m) / sd <= -SIGMA):
            i += 1
            continue
        entry, stop = c[i], c[i] * (1 - STOP_PCT)
        out = None
        for j in range(i + 1, min(n, i + MAX_HOLD + 1)):
            if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                out = "corrupt"
                break
            tgt = _sma(c, j, W)
            if l[j] <= stop:
                out = (100 * (min(stop, o[j]) / entry - 1), j - i)
                break
            if h[j] >= tgt:
                out = (100 * (max(tgt, o[j]) / entry - 1), j - i)
                break
        if out == "corrupt":
            i = j + 1
            continue
        if out is None:
            j = min(n - 1, i + MAX_HOLD)
            out = (100 * (c[j] / entry - 1), max(1, j - i))
        rows.append({"ret": out[0], "date": d[i], "sym": sym})
        i += max(1, out[1]) + 1


def grade(name, tr):
    if not tr:
        print(f"{name:16}     0")
        return None
    a = np.array([t["ret"] for t in tr])
    dmed = sorted(t["date"] for t in tr)[len(tr) // 2]
    ss = sorted({t["sym"] for t in tr})
    first = set(ss[:len(ss) // 2])
    cells = []
    for tf in (lambda t: t["date"] < dmed, lambda t: t["date"] >= dmed):
        for sf in (lambda t: t["sym"] in first, lambda t: t["sym"] not in first):
            xs = [t["ret"] for t in tr if tf(t) and sf(t)]
            cells.append(float(np.mean(xs)) if xs else float("nan"))
    print(f"{name:16}{len(a):>7}{100*np.mean(a>0):>7.1f}%{np.mean(a):>+8.2f}%"
          f"{np.median(a):>+8.2f}%{a.min():>+8.1f}%  "
          + " ".join(f"{x:+.2f}" for x in cells))
    return {"n": len(a), "win": 100 * float(np.mean(a > 0)),
            "mean": float(np.mean(a)), "worst": float(a.min()), "cells": cells}


def main():
    syms = universe_symbols()
    data = {}
    for sym in syms:
        try:
            df = _load(sym)
            if df is None or len(df) < 260:
                continue
            c = df["close"].tolist()
            v = df["volume"].tolist() if "volume" in df.columns else None
            if not poison_check(c, v)[0]:
                continue
            data[sym] = (c, df["high"].tolist(), df["low"].tolist(),
                         df["open"].tolist(), [str(x)[:10] for x in df.index])
        except Exception:  # noqa: BLE001
            continue
    pre2010 = sum(1 for v in data.values() if v[4][0] < "2007-01-01")
    print(f"symbols usable: {len(data)}  ·  with pre-2007 history: {pre2010}")

    rows = []
    for sym, (c, h, l, o, d) in sorted(data.items()):
        simulate(c, h, l, o, d, sym, rows)

    crisis = [t for t in rows if CRISIS[0] <= t["date"] <= CRISIS[1]]
    post = [t for t in rows if t["date"] > CRISIS[1]]
    full = rows

    print(f"\n{'split':16}{'n':>7}{'win':>8}{'mean':>8}{'median':>8}{'worst':>8}  two-split cells")
    g_post = grade("POST-2010", post)
    g_cris = grade("CRISIS 07-09", crisis)
    g_full = grade("FULL SAMPLE", full)

    print("\n— gates (frozen in SWING_CRISIS_GATES_V1.md) —")
    if g_post:
        print(f"C0 post-2010 reproduces : mean {g_post['mean']:+.2f}% vs published +1.06%, "
              f"win {g_post['win']:.1f}% vs 72.8%  → "
              f"{'PASS' if abs(g_post['mean']-1.06)<=0.15 and abs(g_post['win']-72.8)<=1.5 else 'FAIL'}")
    if g_cris:
        print(f"C1 crisis n >= 100      : {g_cris['n']}  → {'PASS' if g_cris['n']>=100 else 'FAIL'}")
        print(f"C2 crisis mean > 0.00%  : {g_cris['mean']:+.2f}%  → "
              f"{'PASS' if g_cris['mean']>0 else 'FAIL'}")
        print(f"C3 crisis worst >= -40% : {g_cris['worst']:+.1f}%  → "
              f"{'PASS' if g_cris['worst']>=-40 else 'FAIL'}")
    if g_full:
        print(f"C4 full mean >= +0.75%  : {g_full['mean']:+.2f}%  → "
              f"{'PASS' if g_full['mean']>=0.75 else 'FAIL'}")


if __name__ == "__main__":
    main()
