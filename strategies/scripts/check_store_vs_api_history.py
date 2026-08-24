#!/usr/bin/env python3
"""Does the parquet store hold as much history as the API already serves?

TradePro has two datasets and one label. Every Python study reads the parquet
store; the Scanner and the desk read the API (Postgres `ibkr_price_bars`). When
they disagree about how much history a symbol has, the same "per-symbol record"
means different things on two surfaces — and nothing says so.

Found on 2026-08-24 by the research lane, from a question about a UI label: a
worked example on WCC produced 5 trades in Python and 20 on screen.

    WCC   API           5,000 bars from 2006-10-05
          parquet       1,164 bars from 2022-01-03

Of 60 universe symbols checked, 11 were materially shallower locally — 14,322
bars held against 47,328 available, ~70% of the history missing for those names.

WHY IT HAPPENS (diagnosed 2026-08-24, and it is a cap, not an accident):

`bar_cache/providers/ibkr_web_provider.max_history()` returns `365 * 5` days for
any resolution not in its measured table — which includes **1d**. So the parquet
path will not ask for more than five years of daily history, ever. The C# path
has no such limit: `IBKRDailyBackfillService` runs a 15-year BackfillPeriod and
pages BACKWARD with startTime anchoring, because IBKR caps a single response at
~999 bars. IBKR serves the depth; only the Python side declines to ask.

The consequence that matters: **the cap re-applies on every re-seed.** Backfilling
without changing `max_history` would silently truncate to five years again.

Read-only. Compares first bar and bar count per symbol, and exits non-zero if
any symbol is materially shallower locally than the API already serves.

    uv run python scripts/check_store_vs_api_history.py [--limit 40] [--min-ratio 0.9]
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


def _universe() -> list[str]:
    raw = json.load(open(UNIVERSE))
    syms = raw if isinstance(raw, list) else (
        raw.get("symbols") or raw.get("tradeable") or [])
    if syms and isinstance(syms[0], dict):
        syms = [s.get("symbol") or s.get("canonical") for s in syms]
    return [s for s in syms if s]


def _local(sym: str) -> tuple[int, str | None]:
    for tree in ("us_etf", "index_us", "uk_equity"):
        parts = sorted(glob.glob(f"{STORE}/{tree}/{sym}/1d/*.parquet"))
        if not parts:
            continue
        frames = [pd.read_parquet(p) for p in parts]
        df = pd.concat(frames)
        idx = pd.to_datetime(df["ts"] if "ts" in df.columns else df.index, utc=True)
        idx = idx[~idx.duplicated()]
        return len(idx), str(idx.min().date())
    return 0, None


def _api(sym: str, base: str, limit: int = 6000) -> tuple[int, str | None]:
    url = f"{base}/api/integrations/ibkr/bars?symbol={sym}&resolution=1d&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return -1, None
    bars = data.get("bars") or data.get("data") or (data if isinstance(data, list) else [])
    if not bars:
        return 0, None
    first = bars[0]
    ts = first.get("ts") or first.get("t") or first.get("date") or first.get("timestamp")
    return len(bars), str(ts)[:10] if ts else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40, help="symbols to check")
    ap.add_argument("--min-ratio", type=float, default=0.90,
                    help="local/api bar-count ratio below which a symbol is flagged")
    ap.add_argument("--api-base", default=DEFAULT_API)
    args = ap.parse_args()

    syms = _universe()[:args.limit]
    print(f"comparing {len(syms)} symbols · parquet store vs {args.api_base}\n")
    print(f"{'sym':<7}{'local':>8}{'api':>8}{'ratio':>8}  {'local from':<12}{'api from':<12}")

    shallow, local_total, api_total, unreachable, capped = [], 0, 0, 0, 0
    for s in syms:
        ln, lfirst = _local(s)
        an, afirst = _api(s, args.api_base)
        if an < 0:
            unreachable += 1
            continue
        if an == 0:
            continue
        # A response landing exactly on a round 5000 is the API truncating,
        # not the instrument's real age — so the gap is at least this big.
        if an in (1000, 5000, 6000):
            capped += 1
        ratio = ln / an if an else 0.0
        local_total += ln
        api_total += an
        flag = "" if ratio >= args.min_ratio else "  <-- SHALLOW"
        if ratio < args.min_ratio:
            shallow.append((s, ln, an, lfirst, afirst))
        print(f"{s:<7}{ln:>8}{an:>8}{ratio:>7.0%}  {str(lfirst):<12}{str(afirst):<12}{flag}")

    print()
    if unreachable:
        print(f"API unreachable for {unreachable} symbol(s) — result is partial")
    if capped:
        print(f"NOTE: {capped} symbol(s) returned exactly the API's own cap, so "
              f"'available' is a FLOOR — the true depth at IBKR may be greater still.")
    print(f"totals: local {local_total:,} bars vs api {api_total:,} available")
    if api_total:
        print(f"        holding {local_total / api_total:.0%} of what is already served")
    print(f"\nsymbols materially shallower locally: {len(shallow)}")
    for s, ln, an, lf, af in shallow:
        print(f"  {s:<7}{ln:>7} vs {an:<7} from {lf} vs {af}")

    if shallow:
        print("\nCAUSE: ibkr_web_provider.max_history() returns 365*5 days for 1d.")
        print("Backfilling WITHOUT raising that cap will re-truncate to five years.")
    return 1 if shallow else 0


if __name__ == "__main__":
    sys.exit(main())
