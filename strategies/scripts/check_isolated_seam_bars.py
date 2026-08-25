#!/usr/bin/env python3
"""Find ISOLATED adjusted closes sitting in an otherwise-raw series.

Not phantoms, and the distinction decides the fix.

The research lane found HYG 2021-11-30 stored at 65.42 between neighbours of
86.00 and 85.37 — a −23.9% day in a bond ETF that moves ±0.5%, reversing +30.5%
the next day. It gapped an −8% stop and produced a −23.2% "worst trade" that had
been reported as a property of the strategy. They classified it as a phantom bar
needing a phantom check across every source.

It is the adjusted/raw seam, in its most damaging shape. The suspect bar divided
by its raw neighbours gives an implied factor that drifts SMOOTHLY toward 1.0:

    HYG   0.7575 (2021-08)  ->  0.7817 (2022-06)
    SCHD  0.8388 (2021-08)  ->  0.8594 (2022-06)

That is a cumulative dividend adjustment, not corruption. yfinance rows are
dividend-ADJUSTED, ibkr rows are RAW, and when a SINGLE yfinance row lands in a
run of ibkr rows the whole adjustment appears as a one-bar spike. 61% fall on a
month end because the store partitions monthly and the boundary is where a
yfinance backfill met an ibkr series.

WHY IT MATTERS THAT THESE ARE SEAM BARS, NOT PHANTOMS:

  * The `adj_factor` migration FIXES them. They are not a separate class needing
    separate work, and a "phantom check across every source" would be looking
    for the wrong thing.
  * But they are the WORST instance of the seam, because an isolated spike gaps
    through a stop where a gradual convention change never would. A −23% bar in
    a bond ETF manufactures an outlier that survives every filter aimed at
    signal windows.
  * They are detectable NOW, from data we already hold, with no second source —
    which the 79 symbols lacking API coverage cannot say of any cross-store check.

Method: robust local volatility (MAD over 60 bars, ×1.4826), because a naive
standard deviation is inflated by the very bars being detected — HYG's naive
sigma is 1.889% against a robust 0.299%. Flag |z| > 10 with a next-day reversal
of 0.9–1.6×. A genuine crash that bounces reverses by LESS than it fell
(SCHD 2020-03-13, factor 1.099, is the COVID crash and is correctly not a seam
bar); a seam bar over-reverses, because the next day returns to a level the
false one never left.

    uv run python scripts/check_isolated_seam_bars.py
"""
from __future__ import annotations

import argparse, glob, json, os, sys
import numpy as np
import pandas as pd

BASE = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
UNIVERSE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "universe", "tradeable.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=float, default=10.0)
    ap.add_argument("--out", default=None, help="write JSON manifest here")
    args = ap.parse_args()

    syms = [x["symbol"] for x in json.load(open(UNIVERSE))["symbols"]]
    hits = []
    for s in syms:
        fs = sorted(glob.glob(f"{BASE}/{s}/1d/*.parquet"))
        if not fs:
            continue
        df = pd.concat([pd.read_parquet(f) for f in fs])
        idx = pd.to_datetime(df["ts"] if "ts" in df.columns else df.index, utc=True)
        df = df.assign(_i=idx).drop_duplicates("_i").set_index("_i").sort_index()
        c = df["close"].astype(float)
        r = c.pct_change()
        mad = r.rolling(60).apply(lambda x: np.median(np.abs(x - np.median(x))), raw=True) * 1.4826
        z = (r / mad).abs()
        rev = r.shift(-1) / -r
        for t in z.index[(z > args.z) & (rev.between(0.9, 1.6))]:
            k = df.index.get_loc(t)
            if k == 0 or k + 1 >= len(df):
                continue
            neighbours = (c.iloc[k - 1] + c.iloc[k + 1]) / 2
            nxt = df.index[k + 1]
            hits.append({
                "symbol": s, "date": str(t.date()), "source": df.loc[t, "source"],
                "stored": round(float(c.iloc[k]), 4),
                "raw_neighbours": round(float(neighbours), 4),
                "implied_factor": round(float(c.iloc[k] / neighbours), 4),
                "move_pct": round(float(r[t]) * 100, 2),
                "robust_z": round(float(z[t]), 1),
                "month_end": bool(nxt.month != t.month),
            })

    print(f"isolated seam bars across {len(syms)} universe symbols: {len(hits)}")
    if hits:
        h = pd.DataFrame(hits)
        print(f"  symbols affected : {h.symbol.nunique()}")
        print(f"  by source        : {dict(h.groupby('source').size())}")
        print(f"  month-end        : {int(h.month_end.sum())} of {len(h)}")
        print(f"  implied factors  : {h.implied_factor.min():.3f} .. {h.implied_factor.max():.3f}")
        print("\n  worst by |z|:")
        print(h.reindex(h.robust_z.sort_values(ascending=False).index).head(8).to_string(index=False))
        if args.out:
            json.dump({"generated_utc": "2026-08-25", "count": len(hits),
                       "note": "adjusted closes isolated in a raw series; fixed by the "
                               "adj_factor migration, NOT a separate phantom class",
                       "rows": hits}, open(args.out, "w"), indent=1)
            print(f"\n  written: {args.out}")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
