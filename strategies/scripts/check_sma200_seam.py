#!/usr/bin/env python3
"""Does the adjusted/raw close seam threaten any Swing 200-SMA entry gate?

Background. The canonical bar store's `close` column mixes two conventions:
rows sourced from yfinance are dividend-ADJUSTED, rows from ibkr / ibkr_web are
RAW, and `adj_factor` is 1.0 everywhere so nothing records which is which. The
Swing rule gates every entry on price being above the 200-SMA, so a 200-day
window spanning both conventions computes that average over mixed units.

This script answers the only question that matters operationally: is any symbol
sitting closer to its 200-SMA than the size of its own bias? If not, the seam
cannot change an entry decision and is safe to carry.

The bias is per-symbol and must be measured, not assumed. A flat 1% assumption
flagged BRK-B as a near-miss on 23 Aug 2026; Berkshire pays no dividend, so its
adjusted and raw series are identical and its true bias is zero. Each symbol's
gap is therefore taken from its own legacy adj_close series.

    bias ≈ (share of the 200-day window sourced from yfinance)
           × (that symbol's median raw-vs-adjusted gap)

Run it before drawing conclusions about a marginal entry, and re-run it if a
symbol drifts close to its 200-SMA during the forward test.

    uv run python scripts/check_sma200_seam.py [--threshold 1.0]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import pandas as pd

STORE = os.path.expanduser("~/.tradepro/bar_cache")
LEGACY = os.path.expanduser("~/.tradepro/cache/yahoo/1d")
UNIVERSE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "universe", "tradeable.json")
WINDOW = 200


def _universe() -> list[str]:
    raw = json.load(open(UNIVERSE))
    syms = raw if isinstance(raw, list) else (
        raw.get("symbols") or raw.get("tradeable") or [])
    if syms and isinstance(syms[0], dict):
        syms = [s.get("symbol") or s.get("canonical") for s in syms]
    return [s for s in syms if s]


def _window(sym: str) -> pd.DataFrame | None:
    parts = sorted(glob.glob(f"{STORE}/us_etf/{sym}/1d/*.parquet"))
    if not parts:
        return None
    df = pd.concat([pd.read_parquet(p) for p in parts[-14:]])
    idx = df["ts"] if "ts" in df.columns else df.index
    df.index = pd.to_datetime(idx, utc=True)
    df = df[~df.index.duplicated()].sort_index().tail(WINDOW)
    return df if len(df) == WINDOW else None


def _own_gap_pct(sym: str) -> float | None:
    """This symbol's median raw-vs-adjusted gap, from its legacy series."""
    path = f"{LEGACY}/{sym}.parquet"
    if not os.path.exists(path):
        return None
    d = pd.read_parquet(path).tail(300)
    if "adj_close" not in d.columns or d.empty:
        return None
    return float(((d["close"] - d["adj_close"]).abs() / d["close"] * 100).median())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="also list symbols within this %% of their 200-SMA")
    args = ap.parse_args()

    rows, unmeasurable = [], []
    for sym in _universe():
        df = _window(sym)
        if df is None or "source" not in df.columns:
            continue
        yf_frac = float((df["source"] == "yfinance").mean())
        if yf_frac == 0:
            continue                      # single convention — no seam
        gap = _own_gap_pct(sym)
        if gap is None:
            unmeasurable.append(sym)
            continue
        sma = float(df["close"].mean())
        last = float(df["close"].iloc[-1])
        rows.append({"sym": sym,
                     "dist_pct": (last - sma) / sma * 100,
                     "bias_pct": yf_frac * gap})

    if not rows:
        print("No symbol has a mixed-convention 200-day window. Seam is not in play.")
        return 0

    d = pd.DataFrame(rows)
    print(f"universe symbols with a MIXED 200-day window : {len(d)}")
    print(f"SMA200 bias                                  : "
          f"median {d.bias_pct.median():.3f}%  max {d.bias_pct.max():.3f}%")
    if unmeasurable:
        print(f"bias NOT measurable (no legacy adj series)   : {len(unmeasurable)} "
              f"{unmeasurable[:8]}")

    flip = d[d.dist_pct.abs() < d.bias_pct]
    print(f"\nsymbols whose 200-SMA gate the seam could FLIP: {len(flip)}")
    if len(flip):
        print(flip.sort_values("dist_pct").to_string(index=False))
    else:
        print("  none — the seam cannot change an entry decision right now")

    near = d[d.dist_pct.abs() < args.threshold]
    print(f"\nwithin {args.threshold}% of their 200-SMA (watch these): {len(near)}")
    if len(near):
        print(near.reindex(near.dist_pct.abs().sort_values().index)
                  .head(10).to_string(index=False))

    # Non-zero exit ONLY when a decision could actually change, so this can be
    # wired into a pre-run check without crying wolf about the seam's existence.
    return 1 if len(flip) else 0


if __name__ == "__main__":
    sys.exit(main())
