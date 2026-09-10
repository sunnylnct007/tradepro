"""SWING_V3_GATES_V1.md runs — thresholds frozen in commit BEFORE this ran.

Control reproduces the live rule (-2.25 sigma, 20d-mean target, 8% stop,
200-SMA floor, 20-session timeout). Variants change EXACTLY ONE thing each:
  Q1 atr_stop : stop = entry - 2.5 * ATR14(entry)   (fill-at-open honesty kept)
  Q2 floor    : skip entries with ATR% < 2.0        (rule otherwise identical)
  Q3 confirm  : after a signal, wait for the first close > prior day's high
                within 3 sessions; enter AT THAT CLOSE (else no trade)
"""
import statistics as st
import sys

import numpy as np

sys.path.insert(0, ".")
from tradepro_strategies.universe import universe_symbols, poison_check  # noqa: E402
from tradepro_strategies.cli.build_universe import _load  # noqa: E402
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    SIGMA, BB_WINDOW as W, STOP_PCT, MAX_HOLD)

MAX_DAY_MOVE = 0.35


def _sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def _atr(h, l, c, i):
    trs = [max(h[j] - l[j], abs(h[j] - c[j - 1]), abs(l[j] - c[j - 1]))
           for j in range(i - 13, i + 1)]
    return sum(trs) / 14


def simulate(c, h, l, o, d, sym, variant, rows):
    n = len(c)
    i = 210
    while i < n - 1:
        m = _sma(c, i, W)
        sd = st.pstdev(c[i - W + 1:i + 1])
        if not (sd > 0 and c[i] > _sma(c, i, 200)
                and (c[i] - m) / sd <= -SIGMA):
            i += 1
            continue
        atr = _atr(h, l, c, i)
        atr_pct = 100 * atr / c[i]
        if variant == "floor" and atr_pct < 2.0:
            i += 1
            continue
        ei = i                      # entry bar
        if variant == "confirm":
            ei = None
            for j in range(i + 1, min(n - 1, i + 4)):
                if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                    break
                if c[j] > h[j - 1]:
                    ei = j
                    break
            if ei is None:
                i += 1
                continue
        entry = c[ei]
        stop = (entry - 2.5 * _atr(h, l, c, ei) if variant == "atr_stop"
                else entry * (1 - STOP_PCT))
        out = None
        for j in range(ei + 1, min(n, ei + MAX_HOLD + 1)):
            if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
                out = "corrupt"
                break
            tgt = _sma(c, j, W)
            if l[j] <= stop:
                out = (100 * (min(stop, o[j]) / entry - 1), j - ei)
                break
            if h[j] >= tgt:
                out = (100 * (max(tgt, o[j]) / entry - 1), j - ei)
                break
        if out == "corrupt":
            i = j + 1
            continue
        if out is None:
            j = min(n - 1, ei + MAX_HOLD)
            out = (100 * (c[j] / entry - 1), max(1, j - ei))
        rows.append({"ret": out[0], "date": d[ei], "sym": sym})
        i = ei + max(1, out[1]) + 1


def grade(name, tr, control_mean=None):
    if not tr:
        print(f"{name:10}     0")
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
    print(f"{name:10}{len(a):>6}{100*np.mean(a>0):>6.1f}%{np.mean(a):>+7.2f}%"
          f"{np.median(a):>+7.2f}%{a.min():>+7.1f}%  "
          + " ".join(f"{x:+.2f}" for x in cells), flush=True)
    return {"n": len(a), "win": 100 * np.mean(a > 0), "mean": float(np.mean(a)),
            "worst": float(a.min()), "cells": cells}


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
    print(f"loaded {len(data)} of {len(syms)}")
    print(f"{'variant':10}{'n':>6}{'win':>7}{'mean':>8}{'median':>8}{'worst':>8}  cells")
    results = {}
    for variant in ("control", "atr_stop", "floor", "confirm"):
        rows = []
        for sym, (c, h, l, o, d) in sorted(data.items()):
            simulate(c, h, l, o, d, sym, variant, rows)
        results[variant] = grade(variant, rows)
    ctl = results["control"]
    print("\n— gate verdicts (bars from SWING_V3_GATES_V1.md) —")
    q1 = results["atr_stop"]
    if q1 and ctl:
        ok = (q1["mean"] >= ctl["mean"] - 0.05
              and q1["worst"] >= ctl["worst"] + 3
              and all(x > 0 for x in q1["cells"])
              and abs(q1["n"] - ctl["n"]) <= 0.10 * ctl["n"])
        print(f"Q1 atr_stop: {'PASS — replace the stop' if ok else 'FAIL — 8% stop stands'}")
    q2 = results["floor"]
    if q2 and ctl:
        # gates ask about the EXCLUDED trades: reconstruct = control minus floor
        print(f"Q2 floor: kept mean {q2['mean']:+.2f} vs control {ctl['mean']:+.2f} "
              f"(excluded n={ctl['n']-q2['n']}) — see doc bars")
    q3 = results["confirm"]
    if q3 and ctl:
        ok = (q3["n"] >= 300 and q3["win"] >= 65 and q3["mean"] >= 0.75
              and all(x > 0 for x in q3["cells"]) and q3["worst"] >= -25
              and q3["mean"] > ctl["mean"])
        print(f"Q3 confirm: {'PASS — beats control' if ok else 'FAIL — entry stays at the signal close'}")


if __name__ == "__main__":
    main()
