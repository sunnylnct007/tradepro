"""A ranking rule that survives a WIDER pool.

Gates and predictions pre-registered in RANKING_GATES_V2.md (9bcb500) BEFORE
this ran.

reward:risk won on the 244-name universe and breaks on 991 names: its numerator
grows with volatility while its denominator is a fixed 8%, so volatile names
score higher without being better trades. Six candidates spanning "normalise
fully" to "not at all", plus one that fixes the POOL instead of the rank.
"""
from __future__ import annotations

import glob
import math
import os
import statistics as st
from collections import defaultdict

from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals import mean_reversion as mr

STOP = mr.STOP_PCT


def sma(x, i, n):
    return sum(x[i - n + 1:i + 1]) / n


def atr_pct(h, l, c, i, n=14):
    trs = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1]))
           for k in range(i - n + 1, i + 1)]
    return 100 * (sum(trs) / n) / c[i]


def scan(syms):
    out = []
    for si, s in enumerate(syms):
        d = _load(s)
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
            e = c[i]; stop = e * (1 - STOP); r = None
            for j in range(i + 1, min(len(c), i + mr.MAX_HOLD + 1)):
                t = sma(c, j, mr.BB_WINDOW)
                if l[j] <= stop: r = (100 * (min(stop, o[j]) / e - 1), j); break
                if h[j] >= t: r = (100 * (max(t, o[j]) / e - 1), j); break
            if r is None:
                j = min(len(c) - 1, i + mr.MAX_HOLD); r = (100 * (c[j] / e - 1), j)
            up = mr.target_price(c, i) / e - 1
            a = atr_pct(h, l, c, i)
            out.append({"sym": s, "si": si, "d0": dt[i], "d1": dt[r[1]],
                        "pct": r[0], "up": up, "atr": max(a, 1e-6)})
            i = r[1] + 1
    return out


RULES = {
    "N0 reward:risk":      lambda t, med: -(t["up"] / STOP),
    "N1 up / atr":         lambda t, med: -(100 * t["up"] / t["atr"]),
    "N2 up / sqrt(atr)":   lambda t, med: -(100 * t["up"] / math.sqrt(t["atr"])),
    "N3 rr - 0.1*atr":     lambda t, med: -((t["up"] / STOP) - 0.1 * t["atr"]),
    "N4 calmest name":     lambda t, med: t["atr"],
    "N5 rr, calm half":    lambda t, med: (0 if t["atr"] <= med else 1, -(t["up"] / STOP)),
}


def sim(trades, cap, key, med):
    byday = defaultdict(list)
    for t in trades:
        byday[t["d0"]].append(t)
    openq, taken = [], []
    for day in sorted(byday):
        openq = [x for x in openq if x >= day]
        free = cap - len(openq)
        if free <= 0:
            continue
        for t in sorted(byday[day], key=lambda z: key(z, med))[:free]:
            openq.append(t["d1"]); taken.append(t)
    return taken


def line(name, tk):
    return (f"{name:<20}{len(tk):>7}{st.mean(t['pct'] for t in tk):>8.2f}%"
            f"{min(t['pct'] for t in tk):>9.1f}%"
            f"{100*sum(1 for t in tk if t['pct']>0)/len(tk):>7.1f}%")


if __name__ == "__main__":
    uni = list(universe_symbols())
    wide = sorted({os.path.basename(p)
                   for p in glob.glob("/Users/skumar/.tradepro/bar_cache/us_etf/*")
                   if os.path.isdir(p)})
    pools = {}
    for label, syms in (("NARROW 244", uni), ("WIDE 991", wide)):
        tr = scan(syms)
        med = st.median(t["atr"] for t in tr)
        pools[label] = (tr, med)
        print(f"\n{'='*60}\n{label} — {len(tr)} signals, median ATR {med:.2f}%\n{'='*60}")
        print(f"{'rule':<20}{'taken':>7}{'mean':>9}{'worst':>9}{'win':>8}")
        for n, k in RULES.items():
            print(line(n, sim(tr, 12, k, med)))

    tr, med = pools["WIDE 991"]
    mid = sorted(t["d0"] for t in tr)[len(tr) // 2]
    cells = {
        "time 1st": [t for t in tr if t["d0"] < mid],
        "time 2nd": [t for t in tr if t["d0"] >= mid],
        "sym even": [t for t in tr if t["si"] % 2 == 0],
        "sym odd":  [t for t in tr if t["si"] % 2 == 1],
    }
    print(f"\n{'='*60}\nTWO-SPLIT on the WIDE pool — improvement over N0, cap 12\n{'='*60}")
    print(f"{'rule':<20}" + "".join(f"{k:>12}" for k in cells))
    for n, k in RULES.items():
        if n.startswith("N0"):
            continue
        row = f"{n:<20}"
        for cn, sub in cells.items():
            m = st.median(t["atr"] for t in sub)
            base = st.mean(t["pct"] for t in sim(sub, 12, RULES["N0 reward:risk"], m))
            got = st.mean(t["pct"] for t in sim(sub, 12, k, m))
            row += f"{got-base:>+11.2f}%"
        print(row)

# ── RESULT, 25 Aug 2026 ────────────────────────────────────────────────────
#
# **NOTHING PASSES. reward:risk stays, and the conclusion is that the problem
# is the POOL, not the RANK.**
#
#                       NARROW 244          WIDE 991
#                     mean    worst       mean    worst
#   N0 reward:risk   0.76%   -17.7%      0.66%   -21.6%   <- best on BOTH
#   N1 up / atr      0.67%   -23.2%      0.57%   -23.2%
#   N2 up/sqrt(atr)  0.70%   -23.2%      0.64%   -21.6%
#   N3 rr - 0.1*atr  0.71%   -23.2%      0.64%   -21.6%
#   N4 calmest       0.54%   -23.2%      0.54%   -23.2%
#   N5 rr calm half  0.64%   -23.2%      0.63%   -21.6%
#
# W1 required beating N0 on the wide pool. Every candidate LOSES to it, and the
# two-split confirms it is not noise: on the wide pool every rule is negative
# or zero in every one of the four cells.
#
# PREDICTIONS vs RESULT — I got the winner wrong again, and in the same
# direction as last time:
#   * I predicted N1 (up/atr) would win W1 because ATR captures gaps and gaps
#     are what kill a fixed stop. N1 is the WORST of the normalised set on the
#     wide pool and is negative in all four cells.
#   * I predicted N4 would fail. It does, worst of all at 0.54%.
#   * I said N5 was the one I most wanted to win, on the grounds that it adds
#     no parameter. It does not.
#   * I flagged N2 as the dangerous case — a half-normalisation with a free
#     exponent winning by a nose would be tuning, not insight. It did not win
#     at all, so the trap did not need to spring.
#
# WHAT THIS OVERTURNS, and it is my own diagnosis from three hours ago.
# `universe_width_v1` observed that the extra 747 names score HIGHER on
# reward:risk (0.83 vs 0.75) and take 27% of slots while delivering less, and I
# concluded the ranking rule was volatility-biased and that correcting the bias
# would let the pool widen. **The observation was right and the inference was
# wrong.** Correcting the bias — by any of four different corrections, mild to
# aggressive — makes results worse on BOTH pools, not better.
#
# So the extra names do not underperform BECAUSE the ranking over-selects
# them. They underperform for a reason this study does not identify, and
# ranking is simply not the lever. I am recording that as an open question
# rather than reaching for a second story.
#
# ONE OBSERVATION WORTH KEEPING: N0 is the only rule that returns -17.7% as the
# narrow-pool worst trade. Every other rule hits -23.2%, which is the HYG
# phantom bar. reward:risk uniquely declines that trade, in v1 and again here.
# That is twice now, on different candidate sets, which makes it a property of
# the rule rather than a coincidence — a signal whose upside cannot justify a
# fixed stop is exactly the one a phantom crash manufactures.
#
# PRACTICAL CONSEQUENCE:
#   * Keep reward:risk. Two independent studies, eleven rival rules, unbeaten.
#   * KEEP THE UNIVERSE NARROW. Expansion is blocked on the liquidity gate —
#     which needs the volume repair — and not on ranking. There is no clever
#     score that makes a heterogeneous pool safe.
