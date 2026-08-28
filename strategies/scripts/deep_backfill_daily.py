#!/usr/bin/env python3
"""Deep 1d backfill — ONE chunked fetch per symbol, not one per month.

WHY NOT `tradepro-bar-cache-harvest --from 2006-...`:

BarStore.get() walks the requested range PARTITION BY PARTITION and issues a
provider call for each one it lacks. Partitions are monthly, so a 20-year
backfill is ~240 requests per symbol and ~58,000 across a 244-name universe.
Measured 28 Aug 2026: twelve minutes in, that path had written ZERO partitions,
and it would have run for many hours while competing with the live daemons for
the single IBKR session. That is the same self-inflicted request storm that
started this whole line of work.

The provider now chunks a wide daily window itself (1000-day slices, sized
under IBKR's 1000-bar response cap), so ONE call covering twenty years costs
about 8 requests instead of 240 — roughly a 30x reduction.

So: ask the provider once, split the answer into monthly partitions, and hand
each to the store's normal write path so manifests, validation and the schema
stay exactly as the harvest would have produced them.

SAFETY:
  * Never overwrites a partition that already holds MORE rows than the slice
    being written. Deep history is additive; it must not trample the recent
    bars the live strategies read.
  * ibkr_web only. yfinance closes are dividend-adjusted and would widen the
    raw/adjusted seam this store is already trying to close.
  * Skips the CURRENT month entirely — that partition belongs to the nightly
    harvest and may hold a settled bar this job would replace with a partial.

    uv run python scripts/deep_backfill_daily.py [--years 20] [--limit N]
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time

log = logging.getLogger("deep_backfill")
UTC = dt.timezone.utc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=20)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--asset", default="us_etf")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    import pandas as pd
    from pathlib import Path
    from tradepro_strategies.bar_cache.store import BarStore
    from tradepro_strategies.bar_cache.asset_class import get_asset_class
    from tradepro_strategies.bar_cache.providers.ibkr_web_provider import IBKRWebProvider
    from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin  # noqa: F401
    from tradepro_strategies.universe import harvest_symbols

    base = Path(os.path.expanduser("~/.tradepro/bar_cache"))
    store = BarStore(base_dir=base, provider_chain=["ibkr_web"])
    plugin = get_asset_class(args.asset)
    prov = IBKRWebProvider()

    syms = harvest_symbols(str(base / args.asset))
    if args.limit:
        syms = syms[:args.limit]

    end = dt.datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=365 * args.years)
    this_month = f"{end.year:04d}-{end.month:02d}"

    log.info("deep backfill: %d symbols, %s -> %s (skipping current month %s)",
             len(syms), start.date(), end.date(), this_month)

    deepened = unchanged = failed = 0
    for n, sym in enumerate(syms, 1):
        t0 = time.time()
        try:
            df, meta = prov.fetch(sym, args.asset, "1d", start, end)
        except Exception as exc:  # noqa: BLE001 — one dead symbol must not stop the sweep
            log.warning("%-6s FETCH FAILED: %s", sym, str(exc)[:110])
            failed += 1
            continue
        if df is None or df.empty:
            log.warning("%-6s no bars returned", sym)
            failed += 1
            continue

        wrote = 0
        for part, chunk in df.groupby(df.index.strftime("%Y-%m")):
            if part >= this_month:
                continue                      # nightly harvest owns the live month
            path = store._partition_path(sym, args.asset, "1d", part)
            if path.exists():
                try:
                    if len(pd.read_parquet(path)) >= len(chunk):
                        continue              # already as deep or deeper
                except Exception:             # noqa: BLE001 — unreadable: rewrite it
                    pass
            try:
                p_start, p_end = store._partition_range(plugin, part)
                store._write_partition(
                    df=chunk, plugin=plugin, canonical=sym,
                    asset_class=args.asset, resolution="1d", partition=part,
                    partition_start=p_start, partition_end=p_end,
                    partition_path=path,
                    manifest_path=store._manifest_path(sym, args.asset, "1d", part),
                    provider_used="ibkr_web", provider_meta=dict(meta or {}),
                    fetched_by="deep_backfill_daily",
                    merge=True,          # never drop a cached row
                )
                wrote += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("%-6s %s write failed: %s", sym, part, str(exc)[:90])

        if wrote:
            deepened += 1
        else:
            unchanged += 1
        log.info("%-6s %5d bars  earliest %s  chunks=%s  +%d partitions  %.0fs  [%d/%d]",
                 sym, len(df), str(df.index.min())[:10], meta.get("chunks", "-"),
                 wrote, time.time() - t0, n, len(syms))

    log.info("DONE: %d deepened, %d already deep, %d failed", deepened, unchanged, failed)
    return 0 if deepened or unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
