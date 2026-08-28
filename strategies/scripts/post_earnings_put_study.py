#!/usr/bin/env python3
"""GRADED run for POST_EARNINGS_PUT_GATES_V1.md.

The gates were committed BEFORE this script existed. It implements them and
reports pass/fail per gate — it does not choose them, and it must never be
edited to make a gate pass. If a gate fails, that is the result.

THE STRATEGY (owner, 28 Aug 2026):
    "MRVL is a good stock and was trading at 240 before quarterly result and
     now it corrected to 220 so i can safely play a put at 195. this strategy
     can work normally after every quarterly results"
    "...we will consider only symbols we happy to hold"
    universe decision: all 244, sized by volatility.

WHAT IS DELIBERATELY PESSIMISTIC, so a pass means something:
  * Premium is priced at realised vol measured BEFORE the drop, not after.
    Post-earnings IV crushes; using post-drop vol would credit us premium the
    market would not actually pay.
  * Costs are charged: commission per contract plus half the bid-ask, modelled
    as a share of premium.
  * The null (G3) uses the SAME symbols and the SAME vol-scaled sizing, so the
    comparison isolates the earnings trigger rather than the universe.

    uv run python scripts/post_earnings_put_study.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import math
import os
import statistics as st
import sys

OTM = 0.10          # strike 10% below the post-drop close
DTE = 30            # sessions held
DROP = -0.08        # the report-day move that defines "corrected"
TARGET_VOL = 0.35   # annualised; sizing anchor
SIZE_CAP = 2.0      # never more than 2x base size
COMMISSION = 1.0    # $ per contract, per side
SPREAD_FRAC = 0.05  # half-spread as a share of premium

EARN = os.path.expanduser("~/.tradepro/research/earnings_history.json")
STORE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")


def _load(sym):
    import pandas as pd
    parts = sorted(glob.glob(f"{STORE}/{sym}/1d/*.parquet"))
    if not parts:
        return None, None
    df = pd.concat([pd.read_parquet(p) for p in parts])
    idx = pd.to_datetime(df["ts"] if "ts" in df.columns else df.index, utc=True)
    df = df.assign(_i=idx).drop_duplicates("_i").set_index("_i").sort_index()
    c = [float(x) for x in df["close"].tolist()]
    d = [str(x)[:10] for x in df.index]
    return c, d


def _trade(pricer, c, j, vol_lookback_end):
    """One cash-secured put opened at bar j. Returns (pct_of_collateral, vol)."""
    lo = vol_lookback_end - 60
    if lo < 0 or j + DTE >= len(c):
        return None
    rets = [c[k] / c[k - 1] - 1 for k in range(lo, vol_lookback_end)]
    sd = st.pstdev(rets)
    if sd <= 0 or c[j] <= 0:
        return None
    iv = sd * math.sqrt(252)
    spot = c[j]
    K = round(spot * (1 - OTM), 2)
    prem = pricer.price(spot, K, DTE / 252.0, iv, "put")
    if prem <= 0:
        return None
    cost = prem * SPREAD_FRAC + COMMISSION / 100.0     # per share
    net = prem - cost - max(0.0, K - c[j + DTE])
    return 100.0 * net / K, iv


def _size(vol):
    """Vol-scaled sizing — the owner's choice: cap the tail by SIZE, not by
    excluding names. A 70%-vol name gets half the collateral of a 35%-vol one."""
    if vol <= 0:
        return 0.0
    return min(SIZE_CAP, TARGET_VOL / vol)


def _stats(rows):
    """rows = [(pct, weight, year)]"""
    if not rows:
        return None
    pct = [r[0] for r in rows]
    w = [r[1] for r in rows]
    tw = sum(w) or 1.0
    return {
        "n": len(rows),
        "win": 100.0 * sum(1 for p in pct if p > 0) / len(pct),
        "mean_w": sum(p * q for p, q in zip(pct, w)) / tw,
        "mean_u": st.mean(pct),
        "median": st.median(pct),
        "p5": sorted(pct)[max(0, len(pct) // 20)],
        "worst": min(pct),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    from tradepro_strategies.quant_engine.options.black_scholes import BlackScholesPricer
    pricer = BlackScholesPricer()

    if not os.path.exists(EARN):
        print(f"FATAL: no earnings history at {EARN} — run fetch_earnings_history.py")
        return 2
    earnings = json.load(open(EARN))

    real, null, crush = [], [], []
    symbols_used = set()

    for sym, dates in earnings.items():
        if not dates:
            continue
        c, d = _load(sym)
        if not c or len(c) < 400:
            continue
        pos = {day: i for i, day in enumerate(d)}

        # ---- the strategy: the session AFTER a report that dropped >= 8% ----
        for day in dates:
            i = pos.get(day)
            if i is None or i < 100 or i + 1 >= len(c):
                continue
            j = i + 1                     # first session after the report
            if c[j] / c[j - 1] - 1 > DROP:
                continue                  # it did not correct
            got = _trade(pricer, c, j, vol_lookback_end=j - 5)   # vol BEFORE the drop
            if not got:
                continue
            pct, vol = got
            real.append((pct, _size(vol), int(day[:4])))
            symbols_used.add(sym)
            # G6: the same trade priced on POST-drop vol, to show the direction
            got2 = _trade(pricer, c, j, vol_lookback_end=j)
            if got2:
                crush.append((got2[0], _size(got2[1]), int(day[:4])))

        # ---- the null: same symbol, same sizing, NON-earnings entries ----
        earn_idx = {pos[x] for x in dates if x in pos}
        for j in range(100, len(c) - DTE, 11):
            if any(abs(j - e) <= 3 for e in earn_idx):
                continue                  # keep the null clear of report weeks
            got = _trade(pricer, c, j, vol_lookback_end=j - 5)
            if got:
                null.append((got[0], _size(got[1]), int(d[j][:4])))

    R, N, C = _stats(real), _stats(null), _stats(crush)
    if not R:
        print("FATAL: no qualifying events")
        return 2

    half1 = _stats([r for r in real if r[2] < 2023])
    half2 = _stats([r for r in real if r[2] >= 2023])

    print("=" * 72)
    print("POST-EARNINGS CASH-SECURED PUT — graded against "
          "POST_EARNINGS_PUT_GATES_V1.md")
    print("=" * 72)
    print(f"  symbols contributing : {len(symbols_used)}")
    print(f"  entry   : session after a report where the stock fell >= {abs(DROP):.0%}")
    print(f"  strike  : {OTM:.0%} OTM · {DTE} sessions · premium at PRE-drop vol")
    print(f"  costs   : ${COMMISSION:.0f}/contract + {SPREAD_FRAC:.0%} of premium as half-spread")
    print(f"  sizing  : min({SIZE_CAP}, {TARGET_VOL:.0%} / symbol vol)\n")

    def line(lab, s):
        if not s:
            print(f"  {lab:<26} —")
            return
        print(f"  {lab:<26} n={s['n']:<5} win {s['win']:>5.1f}%  "
              f"mean(w) {s['mean_w']:>+6.2f}%  median {s['median']:>+6.2f}%  "
              f"p5 {s['p5']:>+7.2f}%")

    line("post-earnings (GRADED)", R)
    line("null: non-earnings", N)
    line("post-drop vol (G6 ref)", C)
    line("half 1  (2020-2022)", half1)
    line("half 2  (2023-2026)", half2)

    edge = R["mean_w"] - (N["mean_w"] if N else 0.0)
    gates = [
        ("V0", "events >= 300",                       R["n"] >= 300,          f"{R['n']}"),
        ("G1", "win rate >= 80%",                     R["win"] >= 80.0,       f"{R['win']:.1f}%"),
        ("G2", "mean/trade (size-wtd, net) > +0.75%", R["mean_w"] > 0.75,     f"{R['mean_w']:+.2f}%"),
        ("G3", "beats null by >= 0.5pt",              edge >= 0.5,            f"{edge:+.2f}pt"),
        ("G4", "p5 >= -8%",                           R["p5"] >= -8.0,        f"{R['p5']:+.2f}%"),
        ("G5", "both halves pass G1 and G2",
         bool(half1 and half2 and half1["win"] >= 80 and half2["win"] >= 80
              and half1["mean_w"] > 0.75 and half2["mean_w"] > 0.75),
         f"h1 {half1['win']:.0f}%/{half1['mean_w']:+.2f}% · h2 {half2['win']:.0f}%/{half2['mean_w']:+.2f}%"
         if half1 and half2 else "insufficient"),
        ("G6", "survives IV crush (G2 on pre-drop)",  R["mean_w"] > 0.75,     f"{R['mean_w']:+.2f}%"),
    ]
    print("\n  GATES")
    for gid, desc, ok, val in gates:
        print(f"    {gid}  {'PASS' if ok else 'FAIL'}  {desc:<38} {val}")

    failed = [g[0] for g in gates if not g[1 + 1]]
    print()
    if failed:
        print(f"  RESULT: FAILED {', '.join(failed)}")
    else:
        print("  RESULT: ALL GATES PASS")
    print("  Licenses a PAPER FORWARD TEST only. Earnings history begins ~Oct 2020,")
    print("  so both halves sit in ONE post-2020 regime; this cannot speak to a")
    print("  sustained bear market. The v3 wheel verdict (DO NOT FUND) is separate.")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
