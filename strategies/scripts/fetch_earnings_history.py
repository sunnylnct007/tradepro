#!/usr/bin/env python3
"""Fetch per-symbol EARNINGS HISTORY for the post-earnings put study.

WHY THIS EXISTS. The central earnings store holds exactly ONE event per symbol
— the next or most recent report. It is a forward calendar, which is right for
the blackout gate ("does a report fall inside this expiry?") and useless for a
backtest, which needs the dates a report ALREADY happened.

Measured 28 Aug 2026: yfinance `earnings_dates` returns 25 rows per symbol
reaching back to roughly 2020-10 — about 20 past events each, so ~4,900 across
the 244-name universe. That is a real sample.

THE LIMIT, stated here so no result built on this file can quietly overstate
itself: it stops at late 2020. A pre-2020 regime split is NOT possible with
this source at any bar depth, because there are no earnings dates to pair the
bars with. Deeper bars do not fix it.

Read-only, resumable, and writes one JSON artifact. Rerunning re-fetches only
symbols not already present unless --refresh is passed.

    uv run python scripts/fetch_earnings_history.py [--limit N] [--refresh]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

OUT = os.path.expanduser("~/.tradepro/research/earnings_history.json")
ARTIFACT_NAME = "earnings_history.json"
log = logging.getLogger("earnings_history")



def save_artifact(obj) -> None:
    """Write locally AND mirror to S3.

    Owner, 29 Aug: "data harvesting is key and i keep on repeating ... we shd
    start storing in our cheap s3 so we can leverage". Everything harvested this
    week lived only on this laptop -- the one whose battery died twice -- which
    also breaks the standing PG+S3 policy. Fail-safe: a dead mirror leaves the
    local file intact and SAYS so rather than reporting success.
    """
    from tradepro_strategies.research_store import save
    save(ARTIFACT_NAME, obj)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    from tradepro_strategies.universe import harvest_symbols
    from tradepro_strategies.yahoo_session import yahoo_session
    import yfinance as yf

    store = os.path.expanduser("~/.tradepro/bar_cache/us_etf")
    syms = harvest_symbols(store)
    if args.limit:
        syms = syms[:args.limit]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    have: dict = {}
    if os.path.exists(OUT) and not args.refresh:
        try:
            have = json.load(open(OUT))
        except Exception:
            have = {}

    todo = [s for s in syms if s not in have]
    log.info("earnings history: %d symbols, %d already held, %d to fetch",
             len(syms), len(syms) - len(todo), len(todo))

    sess = yahoo_session()
    ok = fail = 0
    for n, sym in enumerate(todo, 1):
        try:
            ed = yf.Ticker(sym, session=sess).earnings_dates
            dates = sorted({str(x)[:10] for x in ed.index}) if ed is not None and len(ed) else []
            have[sym] = dates
            ok += 1 if dates else 0
            if not dates:
                fail += 1
        except Exception as exc:  # noqa: BLE001 — one dead symbol must not stop the sweep
            log.warning("%s: %s", sym, str(exc)[:90])
            have[sym] = []
            fail += 1
        if n % 25 == 0:
            save_artifact(have)       # checkpoint; this run is long
            log.info("  %d/%d  ok=%d empty=%d", n, len(todo), ok, fail)
        time.sleep(0.4)                            # be a good citizen

    save_artifact(have)
    tot = sum(len(v) for v in have.values())
    covered = sum(1 for v in have.values() if v)
    log.info("DONE: %d symbols, %d with dates, %d events total -> %s",
             len(have), covered, tot, OUT)
    return 0 if covered else 1


if __name__ == "__main__":
    sys.exit(main())
