#!/usr/bin/env python3
"""Two copies of the same instrument-date must agree.

An INVARIANT, in the sense the research lane drew out of
`check_daily_vs_intraday.py`: it tests something that must be true of any
correct system, against data we already hold, and needs neither a second
opinion nor prior knowledge of what went wrong. Most of what both lanes shipped
this week guards a KNOWN bad state. This one guards an unknown one.

TradePro stores every bar twice — the parquet bar cache that every Python study
reads, and Postgres behind the API that the desk and Scanner read. They are
populated by different code down different paths. Nothing required them to
agree, and for a week they did not.

WHAT IT WOULD HAVE CAUGHT, from a single week:

  * The x100 volume double-conversion. `TXN 2026-08-24` local 221,212,100 vs
    api 2,212,121 — ratio exactly 100.0. Found instead by a person noticing a
    number looked odd.
  * The bad ibkr_web daily write. TXN's stored close was 256.59 against a true
    258.94, which is a 0.9% close disagreement this check reports outright.
  * The adjusted/raw close seam, as a systematic close divergence confined to
    yfinance-sourced date ranges.

READING THE OUTPUT — the ratio is the diagnosis, not just the alarm:

  * A clean power of ten (100.0, 0.01) is a UNITS error. Some layer applied a
    conversion the other did not, or applied it twice.
  * A messy ratio (1.03, 0.87) is CORRUPTION or a different contract — one side
    holds a different instrument's series, or a bad write.
  * Closes disagreeing while volumes match points at a bad WRITE rather than a
    units problem; the two failures do not travel together.

Read-only. Exits non-zero if any symbol disagrees beyond tolerance.

    uv run python scripts/check_store_agrees_with_api.py [--limit 40]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import urllib.request

import pandas as pd

STORE = os.path.expanduser("~/.tradepro/bar_cache")
UNIVERSE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "universe", "tradeable.json")
DEFAULT_API = os.environ.get("TRADEPRO_API_URL", "http://16.60.201.137")

CLOSE_TOL_PCT = 0.10     # a real close is identical; 0.1% is generous
VOL_TOL_RATIO = 1.05     # volumes can differ slightly by consolidation rules


def _universe() -> list[str]:
    raw = json.load(open(UNIVERSE))
    syms = raw if isinstance(raw, list) else (
        raw.get("symbols") or raw.get("tradeable") or [])
    if syms and isinstance(syms[0], dict):
        syms = [s.get("symbol") or s.get("canonical") for s in syms]
    return [s for s in syms if s]


def _local(sym: str, n: int = 10) -> dict:
    for tree in ("us_etf", "index_us", "uk_equity"):
        parts = sorted(glob.glob(f"{STORE}/{tree}/{sym}/1d/*.parquet"))
        if not parts:
            continue
        df = pd.concat([pd.read_parquet(p) for p in parts[-2:]])
        idx = pd.to_datetime(df["ts"] if "ts" in df.columns else df.index, utc=True)
        df = df.assign(_i=idx).drop_duplicates("_i").set_index("_i").sort_index().tail(n)
        return {ts.date(): (float(r["close"]), float(r["volume"]))
                for ts, r in df.iterrows()}
    return {}


def _api(sym: str, base: str) -> dict:
    url = f"{base}/api/integrations/ibkr/price-history?symbol={sym}&period=10d&bar=1d"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode())
    except Exception:
        return {}
    return {pd.Timestamp(b["t"], unit="ms", tz="UTC").date(): (float(b["c"]), float(b["v"]))
            for b in (d.get("bars") or [])}


def _diagnose(ratio: float) -> str:
    """The ratio names the fault. A clean power of ten is a units error."""
    for p, label in ((100.0, "x100 UNITS"), (0.01, "/100 UNITS"),
                     (1000.0, "x1000 UNITS"), (10.0, "x10 UNITS")):
        if abs(ratio - p) / p < 0.01:
            return label
    return "MISMATCH (not a units factor — suspect a bad write or wrong contract)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--api-base", default=DEFAULT_API)
    args = ap.parse_args()

    syms = _universe()[:args.limit]
    print(f"comparing {len(syms)} symbols · parquet store vs {args.api_base}")
    print("an instrument-date held twice must agree\n")

    close_bad, vol_bad, checked, unreachable = [], [], 0, 0
    for s in syms:
        loc, rem = _local(s), _api(s, args.api_base)
        if not rem:
            unreachable += 1
            continue
        for d in sorted(set(loc) & set(rem)):
            lc, lv = loc[d]
            rc, rv = rem[d]
            checked += 1
            if rc and abs(lc - rc) / rc * 100 > CLOSE_TOL_PCT:
                close_bad.append((s, d, lc, rc, abs(lc - rc) / rc * 100))
            if rv and lv:
                ratio = lv / rv
                if ratio > VOL_TOL_RATIO or ratio < 1 / VOL_TOL_RATIO:
                    vol_bad.append((s, d, lv, rv, ratio))

    print(f"instrument-dates compared : {checked}")
    if unreachable:
        print(f"API unreachable for       : {unreachable} symbol(s) — partial result")
    print(f"CLOSE disagreements       : {len(close_bad)}")
    print(f"VOLUME disagreements      : {len(vol_bad)}\n")

    if close_bad:
        print("CLOSE — a price held twice and differing means one copy is WRONG.")
        print("Volumes matching alongside points at a bad write, not a units problem.")
        for s, d, lc, rc, pct in close_bad[:10]:
            print(f"  {s:<7}{d}  local {lc:>10.2f}  api {rc:>10.2f}  {pct:>6.2f}%")
        print()

    if vol_bad:
        ratios = sorted({round(r, 1) for *_, r in vol_bad})
        print(f"VOLUME — distinct ratios seen: {ratios[:8]}")
        print(f"  {_diagnose(vol_bad[0][4])}")
        for s, d, lv, rv, r in vol_bad[:10]:
            print(f"  {s:<7}{d}  local {lv:>15,.0f}  api {rv:>13,.0f}  x{r:>7.1f}")

    return 1 if (close_bad or vol_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
