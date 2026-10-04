"""NEWS_SHOCK_GATES_V1 — frozen in the MD (commit 3cb8ba3) BEFORE this ran.

S1: one-day drop >= 8% while still above the 200-SMA, liquid (63d median
dollar volume >= $25M, point-in-time). Does the bounce beat a MATCHED
control, does it survive both halves, and how bad is the worst case?

The control is the key construction: for each event, the SAME symbol's mean
forward return over all its other sessions above its 200-SMA. Reported as
EXCESS, so "stocks drift up" cannot masquerade as an edge.
"""
import os
import statistics as st
import sys

sys.path.insert(0, ".")

from tradepro_strategies.cli.momentum_candidates import (  # noqa: E402
    _tradeable, poison_check, _load, BASE_DIR)

DROP = -0.08
LIQ_FLOOR = 25_000_000.0
PX_FLOOR = 5.0
HORIZONS = (5, 10, 20)


def sma(c, i, n):
    return sum(c[i - n + 1:i + 1]) / n


def main() -> int:
    events = []          # one row per shock
    controls = {}        # symbol -> {horizon: mean forward return}
    syms = [s for s in sorted(os.listdir(BASE_DIR)) if _tradeable(s)]

    for sym in syms:
        df = _load(sym)
        if df is None or "volume" not in df.columns:
            continue
        c = [float(x) for x in df["close"].tolist()]
        if len(c) < 300 or not poison_check(c)[0]:
            continue
        v = [float(x or 0) for x in df["volume"].tolist()]
        d = [str(x)[:10] for x in
             (df["date"].tolist() if "date" in df.columns else df.index.tolist())]

        # control: every session above the 200-SMA, this symbol, all horizons
        ctl = {h: [] for h in HORIZONS}
        for i in range(210, len(c) - max(HORIZONS) - 1):
            if c[i] <= sma(c, i, 200):
                continue
            for h in HORIZONS:
                ctl[h].append(100 * (c[i + h] / c[i] - 1))
        if not ctl[10]:
            continue
        controls[sym] = {h: st.mean(ctl[h]) for h in HORIZONS}

        for i in range(210, len(c) - max(HORIZONS) - 1):
            if c[i - 1] <= 0:
                continue
            chg = c[i] / c[i - 1] - 1
            if chg > DROP or c[i] <= sma(c, i, 200) or c[i] < PX_FLOOR:
                continue
            dv = [c[k] * v[k] for k in range(i - 62, i + 1) if v[k]]
            if not dv or st.median(dv) < LIQ_FLOOR:
                continue
            row = {"sym": sym, "date": d[i], "drop": 100 * chg}
            for h in HORIZONS:
                row[f"f{h}"] = 100 * (c[i + h] / c[i] - 1)
            events.append(row)

    for e in events:
        for h in HORIZONS:
            e[f"x{h}"] = e[f"f{h}"] - controls[e["sym"]][h]

    print("=" * 74)
    print(f"S1 events (>=8% one-day drop, above 200-SMA, >=$25M/63d): n={len(events)}")
    for h in HORIZONS:
        f = [e[f"f{h}"] for e in events]
        x = [e[f"x{h}"] for e in events]
        print(f"  +{h:2d}d  raw mean {st.mean(f):+5.2f}%  EXCESS {st.mean(x):+5.2f}%  "
              f"median excess {st.median(x):+5.2f}%  win {100*sum(1 for y in f if y>0)/len(f):4.1f}%  "
              f"worst {min(f):+6.1f}%")

    print("\nG2 — both halves (+10d excess)")
    dates = sorted(e["date"] for e in events)
    mid = dates[len(dates) // 2]
    for lab, g in (("early", [e for e in events if e["date"] < mid]),
                   ("late ", [e for e in events if e["date"] >= mid])):
        x = [e["x10"] for e in g]
        print(f"  {lab} n={len(x):5d}  excess {st.mean(x):+5.2f}%  ({dates[0] if lab=='early' else mid} →)")

    print("\nQ3 — depth bands (+10d)")
    bands = (("-8 to -12%", -12, -8), ("-12 to -20%", -20, -12), ("worse than -20%", -1e9, -20))
    for lab, lo, hi in bands:
        g = [e for e in events if lo <= e["drop"] < hi]
        if not g:
            continue
        x = [e["x10"] for e in g]
        f = [e["f10"] for e in g]
        print(f"  {lab:17s} n={len(g):5d}  excess {st.mean(x):+5.2f}%  "
              f"win {100*sum(1 for y in f if y>0)/len(f):4.1f}%  worst {min(f):+6.1f}%")

    x10 = [e["x10"] for e in events]
    f10 = [e["f10"] for e in events]
    early = [e["x10"] for e in events if e["date"] < mid]
    late = [e["x10"] for e in events if e["date"] >= mid]
    checks = [
        ("V0 n>=1000", len(events) >= 1000, len(events)),
        ("G1 +10d excess >= +1.50%", st.mean(x10) >= 1.50, f"{st.mean(x10):+.2f}%"),
        ("G2 both halves positive", min(st.mean(early), st.mean(late)) > 0,
         f"{st.mean(early):+.2f}/{st.mean(late):+.2f}"),
        ("G3 median excess > 0", st.median(x10) > 0, f"{st.median(x10):+.2f}%"),
        ("G4 worst +10d >= -35%", min(f10) >= -35, f"{min(f10):+.1f}%"),
    ]
    print("\n  — GATES —")
    for lab, ok, got in checks:
        print(f"    {lab:28s} {'PASS' if ok else 'FAIL'}  ({got})")
    print(f"\n  VERDICT: {'ALL PASS' if all(c[1] for c in checks) else 'FAILS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
