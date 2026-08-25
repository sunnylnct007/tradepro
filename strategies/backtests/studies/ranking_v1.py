"""Which signal do you take when there aren't enough slots?

Gates and predictions pre-registered in RANKING_GATES_V1.md, committed b8b82f2
BEFORE this ran.

portfolio_capacity_v1 established that Swing's per-trade quality falls from
+1.10% to +0.52% when concurrency is capped at 8 and signals are taken
first-come-first-served. Over half the edge is lost to WHICH signals get
skipped, so selection is load-bearing, not a refinement.

Only same-day competition is ranked — that is the decision the system actually
faces. Everything is computed from the signal bar alone; nothing looks ahead.
"""
from __future__ import annotations

import statistics as st
from collections import defaultdict

from tradepro_strategies.universe import universe_symbols, poison_check
from tradepro_strategies.cli.build_universe import _load
from tradepro_strategies.signals import mean_reversion as mr

PRIOR_TRADES = 20          # same shrinkage constant the Scanner scorecard uses


def sma(x, i, n):
    return sum(x[i - n + 1:i + 1]) / n


def atr_pct(h, l, c, i, n=14):
    trs = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1]))
           for k in range(i - n + 1, i + 1)]
    return 100 * (sum(trs) / n) / c[i]


def collect():
    """Every Swing signal with the features available AT the signal bar."""
    out = []
    for si, sym in enumerate(universe_symbols()):
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
            e = c[i]; stop = e * (1 - mr.STOP_PCT); res = None
            for j in range(i + 1, min(len(c), i + mr.MAX_HOLD + 1)):
                t = sma(c, j, mr.BB_WINDOW)
                if l[j] <= stop: res = (100 * (min(stop, o[j]) / e - 1), j); break
                if h[j] >= t: res = (100 * (max(t, o[j]) / e - 1), j); break
            if res is None:
                j = min(len(c) - 1, i + mr.MAX_HOLD); res = (100 * (c[j] / e - 1), j)
            m20 = sma(c, i, mr.BB_WINDOW)
            sd = st.pstdev(c[i - mr.BB_WINDOW + 1:i + 1])
            a = atr_pct(h, l, c, i)
            out.append({
                "sym": sym, "si": si, "entry_date": dt[i], "exit_date": dt[res[1]],
                "pct": res[0],
                "sigma": (m20 - c[i]) / sd if sd > 0 else 0.0,
                "rr": ((m20 / e - 1) / mr.STOP_PCT) if e > 0 else 0.0,
                "atr": a,
                "vs200": 100 * (c[i] / sma(c, i, 200) - 1),
            })
            i = res[1] + 1
    return out


def with_own_score(trades):
    """R5 needs each symbol's record BEFORE this trade — no look-ahead."""
    hist = defaultdict(list)
    base_n, base_sum = 0, 0.0
    for t in sorted(trades, key=lambda x: x["entry_date"]):
        own = hist[t["sym"]]
        base = (base_sum / base_n) if base_n else 0.0
        n = len(own)
        w = n / (n + PRIOR_TRADES)
        t["own"] = w * (sum(own) / n if n else 0.0) + (1 - w) * base
        own.append(t["pct"]); base_n += 1; base_sum += t["pct"]
    return trades


RULES = {
    "R0 alphabetical":      lambda t: t["sym"],
    "R1 deepest sigma":     lambda t: -t["sigma"],
    "R2 best reward:risk":  lambda t: -t["rr"],
    "R3 lowest ATR":        lambda t: t["atr"],
    "R4 furthest o/ 200":   lambda t: -t["vs200"],
    "R5 own record":        lambda t: -t["own"],
}


def simulate(trades, cap, key):
    """First-come by DAY; within a day, rank the competitors by `key`."""
    byday = defaultdict(list)
    for t in trades:
        byday[t["entry_date"]].append(t)
    open_until, taken = [], []
    for day in sorted(byday):
        open_until = [x for x in open_until if x >= day]
        free = cap - len(open_until)
        if free <= 0:
            continue
        for t in sorted(byday[day], key=key)[:free]:
            open_until.append(t["exit_date"])
            taken.append(t)
    return taken


def grade(trades, caps=(8, 15)):
    print(f"{'rule':<22}" + "".join(f"{'cap '+str(c):>22}" for c in caps))
    print(f"{'':<22}" + "".join(f"{'n':>7}{'mean':>8}{'worst':>7}" for _ in caps))
    res = {}
    for name, key in RULES.items():
        row = f"{name:<22}"
        res[name] = {}
        for cap in caps:
            tk = simulate(trades, cap, key)
            m = st.mean(t["pct"] for t in tk); w = min(t["pct"] for t in tk)
            res[name][cap] = (len(tk), m, w)
            row += f"{len(tk):>7}{m:>7.2f}%{w:>7.1f}"
        print(row)
    return res


if __name__ == "__main__":
    tr = with_own_score(collect())
    print(f"{len(tr)} Swing signals\n")
    print("=" * 78); print("FULL SAMPLE"); print("=" * 78)
    full = grade(tr)

    mid = sorted(t["entry_date"] for t in tr)[len(tr) // 2]
    cells = {
        "time 1st half": [t for t in tr if t["entry_date"] < mid],
        "time 2nd half": [t for t in tr if t["entry_date"] >= mid],
        "symbols even":  [t for t in tr if t["si"] % 2 == 0],
        "symbols odd":   [t for t in tr if t["si"] % 2 == 1],
    }
    print("\n" + "=" * 78)
    print("TWO-SPLIT — improvement over the alphabetical control, per cell, cap 8")
    print("=" * 78)
    print(f"{'rule':<22}" + "".join(f"{k:>16}" for k in cells))
    for name, key in RULES.items():
        if name.startswith("R0"):
            continue
        row = f"{name:<22}"
        for cname, sub in cells.items():
            ctrl = st.mean(t["pct"] for t in simulate(sub, 8, RULES["R0 alphabetical"]))
            got = st.mean(t["pct"] for t in simulate(sub, 8, key))
            row += f"{got - ctrl:>+15.2f}%"
        print(row)

# ── RESULT, 25 Aug 2026 ────────────────────────────────────────────────────
#
# **R2 (best reward:risk) is the only rule that passes, and my prediction was
# wrong.** I predicted R1 (deepest sigma) on the reasoning that it is the only
# candidate expressing the rule's own thesis. R1 fails: it is NEGATIVE in three
# of the four split cells and actually loses to the alphabetical control at
# cap 15.
#
# Full sample, per-trade mean:
#
#                        cap 8    cap 15    worst (cap 8)
#   R0 alphabetical      0.52%     0.69%      -23.2%
#   R1 deepest sigma     0.57%     0.68%      -14.9%
#   R2 best reward:risk  0.68%     0.82%      -17.7%   <-- adopted
#   R3 lowest ATR        0.56%     0.56%      -23.2%
#   R4 furthest o/ 200   0.62%     0.72%      -23.2%
#   R5 own record        0.63%     0.72%      -12.2%
#
# Two-split, improvement over the control at cap 8:
#
#                       time 1st  time 2nd   sym even   sym odd
#   R1 deepest sigma      -0.12     +0.24      -0.08     -0.01   FAIL
#   R2 best reward:risk   +0.04     +0.29      +0.09     +0.07   PASS
#   R3 lowest ATR         -0.10     +0.19      -0.18     -0.07   FAIL
#   R4 furthest o/ 200    -0.00     +0.22      +0.06     -0.00   FAIL
#   R5 own record         -0.03     +0.26      +0.02     -0.01   FAIL
#
# R2 is POSITIVE IN ALL FOUR CELLS. Nothing else is. On the full sample R4 and
# R5 look respectable (+0.10, +0.11 over control) and both collapse the moment
# the sample is split — which is the entire reason the two-split exists, and
# the fourth time this session it has changed a conclusion.
#
# GATES:
#   K1 beats control at cap 8 AND 15    0.68 vs 0.52, 0.82 vs 0.69   PASS
#   K2 survives the time split           +0.04, +0.29                PASS
#   K3 survives the symbol split         +0.09, +0.07                PASS
#   K4 worst trade not worse by >2pt     -17.7% vs -23.2% — BETTER   PASS
#
# THE HONEST WEAKNESS, and I wrote the test for it in advance: "any rule that
# beats the control by a large margin on the full sample but by little in one
# of the four cells" was to be distrusted. **R2's first-half margin is +0.04%,
# which is thin.** It is positive, so it passes as written, but three cells
# carry the result and one barely participates.
#
# That pattern is not specific to R2 — EVERY candidate improves far more in the
# second half (+0.19 to +0.29) than the first (-0.12 to +0.04). The most likely
# reason is that ranking only matters when signals compete for slots, and the
# universe grew over the period, so early years rarely filled 8 slots at all.
# If that is right the first-half cell is mostly measuring "the cap did not
# bind", not "ranking does not work". That is a plausible mechanism and it is
# NOT established — recorded as the caveat on this result rather than as a
# defence of it.
#
# WHY R2 AND NOT R1, mechanically. Both are distance-to-mean measures, but
# sigma is normalised by the symbol's OWN volatility while reward:risk is in
# absolute percent against a stop that is also absolute (-8%). The strategy's
# risk is fixed in percentage terms, so ranking by a volatility-normalised
# quantity ranks by the wrong units — a 3-sigma move on a quiet name may be
# 2% of upside against the same 8% of risk. R2 asks the question the position
# actually faces.
#
# The worst trade also improves 5.5 points, which is not something a selection
# rule is obliged to do, and is consistent with R2 declining trades whose
# upside does not justify the fixed stop.
