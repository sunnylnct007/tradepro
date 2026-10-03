"""UNIVERSE_CUT_GATES_V1 — frozen in the MD (commit 8705596) BEFORE this ran.

C1 liquidity cut, point-in-time. Q1 momentum gates on the cut universe.
Q2 swing unharmed. Q3 extension-at-entry quartiles. Q4 delayed entry.

Entry rules and exits are the LIVE modules' own constants, imported — the
same discipline as every study in this directory.
"""
import statistics as st

import sys
sys.path.insert(0, ".")

from tradepro_strategies.cli.momentum_candidates import (  # noqa: E402
    _tradeable, poison_check, _load, _entry_signal,
    STOP_PCT as M_STOP, TRAIL_PCT as M_TRAIL, MAX_HOLD as M_HOLD, BASE_DIR)
from tradepro_strategies.signals.mean_reversion import (  # noqa: E402
    BB_WINDOW as S_WIN, MAX_HOLD as S_HOLD, SIGMA as S_SIGMA,
    STOP_PCT as S_STOP, TREND_WINDOW)

import os  # noqa: E402

MAX_DAY_MOVE = 0.35
LIQ_FLOOR = 25_000_000.0   # frozen: 63d median dollar volume
PX_FLOOR = 5.0


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def liq_ok(c, v, i):
    if c[i] < PX_FLOOR:
        return False
    dv = [c[k] * v[k] for k in range(i - 62, i + 1) if v[k]]
    return bool(dv) and st.median(dv) >= LIQ_FLOOR


def mom_trade(c, i, delay):
    """Variant C from the live rule: hard -8% + 8% trail, close-checked."""
    e = i + delay
    if e >= len(c) - 1:
        return None
    entry = c[e]
    peak = entry
    for j in range(e + 1, min(len(c) - 1, e + M_HOLD) + 1):
        if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
            return "corrupt"
        if c[j] <= entry * (1 - M_STOP):
            return 100 * (c[j] / entry - 1), j - e
        peak = max(peak, c[j])
        if c[j] <= peak * (1 - M_TRAIL):
            return 100 * (c[j] / entry - 1), j - e
    j = min(len(c) - 1, e + M_HOLD)
    return 100 * (c[j] / entry - 1), j - e


def swing_trade(c, h, l, o, i, delay):
    """The OOS harness trade, verbatim fills (gap-through at the open)."""
    e = i + delay
    if e >= len(c) - 1:
        return None
    entry, stop = c[e], c[e] * (1 - S_STOP)
    for j in range(e + 1, min(len(c), e + S_HOLD + 1)):
        if c[j - 1] <= 0 or abs(c[j] / c[j - 1] - 1) > MAX_DAY_MOVE:
            return "corrupt"
        tgt = sma(c, j, S_WIN)
        if l[j] <= stop:
            return 100 * (min(stop, o[j]) / entry - 1), j - e
        if h[j] >= tgt:
            return 100 * (max(tgt, o[j]) / entry - 1), j - e
    j = min(len(c) - 1, e + S_HOLD)
    return 100 * (c[j] / entry - 1), j - e


def grade_mom(name, rows):
    a = [r["ret"] for r in rows]
    if not a:
        print(f"  {name}: NO TRADES")
        return
    wins = 100 * sum(1 for x in a if x > 0) / len(a)
    mean = st.mean(a)
    hold = st.median([r["bars"] for r in rows])
    pos = sum(x for x in a if x > 0)
    top1 = sum(sorted(a)[-max(1, len(a) // 100):])
    tail = 100 * top1 / pos if pos > 0 else 999
    worst = min(a)
    print(f"  {name:28s} n={len(a):6d} win={wins:5.1f}% mean={mean:+5.2f}% "
          f"hold={hold:.0f}b top1%={tail:4.0f}% worst={worst:+6.1f}%")
    checks = [("V0 n>=1000", len(a) >= 1000), ("G1 win>=45%", wins >= 45),
              ("G2 mean>0", mean > 0), ("G3 hold<=40b", hold <= 40),
              ("G4 top1%<=35%", tail <= 35), ("G5 worst>=-25%", worst >= -25)]
    verdict = "ALL PASS" if all(ok for _, ok in checks) else \
        "FAILS: " + ", ".join(n for n, ok in checks if not ok)
    print(f"  {'':28s} {verdict}")
    return mean, wins, worst


def main() -> int:
    syms = [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]
    mom_all, swing_all = [], []
    for sym in syms:
        df = _load(sym)
        if df is None or "volume" not in df.columns:
            continue
        c = [float(x) for x in df["close"].tolist()]
        if not poison_check(c)[0]:
            continue
        h = [float(x) for x in df["high"].tolist()]
        l = [float(x) for x in df["low"].tolist()]
        o = [float(x) for x in df["open"].tolist()]
        v = [float(x or 0) for x in df["volume"].tolist()]
        n, i = len(c), 210
        # momentum pass
        while i < n - 1:
            if not _entry_signal(c, h, l, i):
                i += 1
                continue
            base = mom_trade(c, i, 0)
            if base == "corrupt" or base is None:
                i += 2 if base == "corrupt" else n
                continue
            row = {"ret": base[0], "bars": base[1],
                   "liq": liq_ok(c, v, i),
                   "ext": 100 * (c[i] / sma(c, i, 200) - 1)}
            for dly in (1, 2):
                t = mom_trade(c, i, dly)
                row[f"d{dly}"] = t[0] if isinstance(t, tuple) else None
            mom_all.append(row)
            i += base[1] + 1
        # swing pass
        i = 210
        while i < n - 1:
            m = sma(c, i, S_WIN)
            sd = st.pstdev(c[i - S_WIN + 1:i + 1])
            if not (sd > 0 and c[i] > sma(c, i, TREND_WINDOW)) \
                    or (c[i] - m) / sd > -S_SIGMA:
                i += 1
                continue
            base = swing_trade(c, h, l, o, i, 0)
            if base == "corrupt" or base is None:
                i += 2 if base == "corrupt" else n
                continue
            row = {"ret": base[0], "bars": base[1], "liq": liq_ok(c, v, i)}
            for dly in (1, 2):
                t = swing_trade(c, h, l, o, i, dly)
                row[f"d{dly}"] = t[0] if isinstance(t, tuple) else None
            swing_all.append(row)
            i += base[1] + 1

    print("=" * 74)
    print("Q1 — MOMENTUM, uncut vs C1 cut (same harness, same rule)")
    grade_mom("uncut (all signals)", mom_all)
    grade_mom("C1 cut ($25M/63d median, $5)", [r for r in mom_all if r["liq"]])

    print("\nQ2 — SWING, uncut vs C1 cut")
    for name, rows in (("uncut", swing_all),
                       ("C1 cut", [r for r in swing_all if r["liq"]])):
        a = [r["ret"] for r in rows]
        print(f"  {name:28s} n={len(a):6d} win={100*sum(1 for x in a if x>0)/len(a):5.1f}% "
              f"mean={st.mean(a):+5.2f}% worst={min(a):+6.1f}%")

    print("\nQ3 — MOMENTUM extension-at-entry quartiles (uncut)")
    exts = sorted(r["ext"] for r in mom_all)
    qs = [exts[len(exts) // 4], exts[len(exts) // 2], exts[3 * len(exts) // 4]]
    labels = ["Q1 freshest", "Q2", "Q3", "Q4 most extended"]
    means = []
    for qi in range(4):
        lo = -1e9 if qi == 0 else qs[qi - 1]
        hi = 1e9 if qi == 3 else qs[qi]
        g = [r["ret"] for r in mom_all if lo < r["ext"] <= hi]
        means.append(st.mean(g))
        print(f"  {labels[qi]:18s} (ext {'<=' if qi<3 else '>'}{(qs[min(qi,2)]):+6.1f}%) "
              f"n={len(g):6d} win={100*sum(1 for x in g if x>0)/len(g):5.1f}% "
              f"mean={st.mean(g):+5.2f}%")
    print(f"  spread best-worst: {max(means)-min(means):.2f}% "
          f"(actionable only if > 0.50% and monotonic-ish)")

    print("\nQ4 — DELAYED ENTRY (both engines, uncut)")
    for name, rows in (("momentum", mom_all), ("swing", swing_all)):
        base = [r["ret"] for r in rows]
        print(f"  {name:9s} d0 n={len(base):6d} mean={st.mean(base):+5.2f}%")
        for dly in (1, 2):
            g = [r[f"d{dly}"] for r in rows if r[f"d{dly}"] is not None]
            print(f"  {'':9s} d{dly} n={len(g):6d} mean={st.mean(g):+5.2f}%  "
                  f"delay cost {st.mean(base)-st.mean(g):+5.2f}%/trade")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
