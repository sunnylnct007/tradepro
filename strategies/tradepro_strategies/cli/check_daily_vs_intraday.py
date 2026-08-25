"""Does each DAILY bar contain its own INTRADAY session?

The check that was missing on 25 Aug 2026, and the one that finally settled a
live trade decision.

A corrupt daily bar is nearly invisible. It has the right shape, a plausible
price, and the right provenance stamp — nothing downstream can tell it from a
good one. TXN's stored 2026-08-24 bar read close 256.59 when the true close was
258.94, and that 0.9% error was the entire difference between a 2.53σ Swing
signal (fires) and 2.32σ (does not). The screen published a trade that did not
exist.

No second provider is needed to catch it. We already hold the 5-minute lane, so
a daily bar can be checked against its own session:

    daily low  must be <= the session's lowest  intraday low
    daily high must be >= the session's highest intraday high

A daily bar that does not contain its own session is wrong, whichever source
stamped it.

RTH ONLY, and that matters. The first version of this check compared a
regular-hours daily bar against a 5m lane that includes extended-hours prints.
Extended hours widen the intraday range, so every liquid name looked broken —
356 "failures", almost all of them an artefact of the window. Restricting both
sides to 13:30–20:00 UTC took it to 98, and to ONE inside the trading universe.
A check that cries wolf is worse than no check: it trains you to skip it.

Tolerance is 0.2%, and a session needs >=70 five-minute bars to be judged at
all — a partial intraday day cannot condemn a daily bar.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter

import pandas as pd

RTH_START, RTH_END = 13.5, 20.0      # UTC hours, US cash session
TOLERANCE = 0.002                    # 0.2%
MIN_INTRADAY_BARS = 70               # of the 78 five-minute bars in an RTH day


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=os.path.expanduser("~/.tradepro/bar_cache/us_etf"))
    ap.add_argument("--month", default="2026-08", help="partition to check, YYYY-MM")
    ap.add_argument("--universe-only", action="store_true",
                    help="only symbols we actually trade — the rest is noise")
    a = ap.parse_args(argv)

    keep: set[str] | None = None
    if a.universe_only:
        from tradepro_strategies.universe import load_universe
        u = load_universe()
        lists = [x for x in u if isinstance(u[x], list)]
        key = next((x for x in ("symbols", "members", "universe") if x in u), lists[0])
        keep = {(s["symbol"] if isinstance(s, dict) else s) for s in u[key]}

    bad: list[tuple[str, str, str, float, float, float, float]] = []
    checked = 0
    for p in sorted(glob.glob(f"{a.root}/*/5m")):
        sym = p.split("/")[-2]
        if keep is not None and sym not in keep:
            continue
        daily_path = f"{a.root}/{sym}/1d/{a.month}.parquet"
        five = sorted(glob.glob(f"{p}/{a.month}*.parquet"))
        if not five or not os.path.exists(daily_path):
            continue
        try:
            i = pd.concat([pd.read_parquet(f) for f in five]).sort_index()
            h = i.index.hour + i.index.minute / 60
            i = i[(h >= RTH_START) & (h < RTH_END)]
            if i.empty:
                continue
            i = i.copy()
            i["_d"] = i.index.date
            g = i.groupby("_d").agg(lo=("low", "min"), hi=("high", "max"), n=("low", "size"))
            g = g[g["n"] >= MIN_INTRADAY_BARS]
            d = pd.read_parquet(daily_path)
            for ts, r in d.iterrows():
                day = ts.tz_convert("UTC").date()
                if day not in g.index:
                    continue
                checked += 1
                lo, hi = float(g.loc[day, "lo"]), float(g.loc[day, "hi"])
                if float(r["low"]) > lo * (1 + TOLERANCE) or float(r["high"]) < hi * (1 - TOLERANCE):
                    bad.append((sym, str(day), str(r.get("source", "?")),
                                float(r["low"]), lo, float(r["high"]), hi))
        except Exception:  # noqa: BLE001 — an unreadable partition is a different problem
            continue

    print(f"checked {checked} daily bars against their own RTH session ({a.month})")
    print(f"bars that do NOT contain their session: {len(bad)}")
    if bad:
        print("by source:", dict(Counter(b[2] for b in bad)))
        print(f"\n{'sym':<7}{'date':<12}{'source':<10}{'d.low':>9}{'rth.low':>9}"
              f"{'d.high':>9}{'rth.high':>10}")
        for b in bad[:40]:
            print(f"{b[0]:<7}{b[1]:<12}{b[2]:<10}{b[3]:>9.2f}{b[4]:>9.2f}{b[5]:>9.2f}{b[6]:>10.2f}")
        if len(bad) > 40:
            print(f"  … and {len(bad) - 40} more")
        print("\nRe-source with: bar_cache_harvest --resolution 1d --force-refresh --ibkr-only")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
