"""Refresh the local Parquet cache for a watchlist.

    uv run tradepro-refresh --watchlist uk --years 10
    uv run tradepro-refresh --symbols AAPL,MSFT --years 5

Idempotent — re-run the same window to top up today's bar; older bars are
merged by timestamp.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from ..watchlists import WATCHLISTS, resolve as resolve_watchlist


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group()
    group.add_argument("--watchlist", default="uk", choices=sorted(WATCHLISTS))
    group.add_argument("--symbols", help="comma-separated list, overrides --watchlist")
    p.add_argument("--provider", default="yahoo", choices=["yahoo", "stooq", "binance"])
    p.add_argument("--interval", default="1d")
    p.add_argument("--years", type=int, default=10)
    p.add_argument(
        "--legacy-cache",
        action="store_true",
        default=False,
        help=(
            "Write prices into the LEGACY yahoo cache (~/.tradepro/cache) "
            "instead of the canonical bar store. Deprecated escape hatch — "
            "the legacy cache is being retired; its readers are migrated."
        ),
    )
    p.add_argument(
        "--eps-snapshot",
        action="store_true",
        default=False,
        help=(
            "After price refresh, record a forwardEps snapshot for every symbol "
            "via yfinance. Run weekly (e.g. Sunday evening cron). ETFs and "
            "symbols without analyst coverage are skipped silently."
        ),
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=365 * args.years)

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        label = "custom"
    else:
        symbols = resolve_watchlist(args.watchlist)
        label = args.watchlist

    print(f"refreshing {len(symbols)} symbols ({label}) from {args.provider} "
          f"[{start.date()}..{end.date()}]")

    ok = 0
    errors: list[str] = []
    for sym in symbols:
        try:
            if args.legacy_cache:
                from ..cache import refresh_symbol
                n = refresh_symbol(args.provider, sym, start, end, args.interval)
            else:
                # CANONICAL STORE (22 Aug 2026). This wrote only into the
                # legacy yahoo cache, whose readers are now migrated — so the
                # weekly EPS lane was spending ~150 Yahoo fetches topping up a
                # cache almost nothing reads, against the very throttle that
                # starves the options screen. golden_daily routes each symbol
                # by the resolver: BARC.L → uk_equity, ^FTSE → index_uk,
                # US names → us_etf.
                from ..ibkr_bars import fetch_daily_bars_with_provenance
                from ..bar_cache.asset_class_resolver import resolve_asset_class
                _ac = resolve_asset_class(sym)
                _ac = "us_etf" if _ac in ("us_equity", "unknown") else _ac
                df, prov = fetch_daily_bars_with_provenance(
                    sym, start, end, asset_class=_ac, fetched_by="refresh",
                    legacy_provider=args.provider)
                n = 0 if df is None else len(df)
                src = (prov or {}).get("source") or "unknown"
                # HONEST REPORTING (22 Aug 2026): a legacy-cache SERVE is not a
                # store refresh. Reporting "757 bars" for bars that were merely
                # read out of the cache we are retiring made the weekly lane
                # claim success while writing nothing to the canonical store.
                if str(src).startswith("legacy"):
                    print(f"  {sym:10s}  {n:>6d} bars  ⚠ served from {src} — "
                          f"NOT written to the {_ac} store")
                    errors.append(f"{sym}: golden chain unavailable (served {src})")
                    continue
                print(f"  {sym:10s}  {n:>6d} bars  [{_ac} · {src}]")
                ok += 1
                continue
            print(f"  {sym:10s}  {n:>6d} bars")
            ok += 1
        except Exception as e:  # noqa: BLE001 — surface any provider error
            errors.append(f"{sym}: {e}")
            print(f"  {sym:10s}  ERROR  {e}", file=sys.stderr)

    print(f"\n{ok}/{len(symbols)} symbols refreshed")
    if errors:
        sys.exit(1)

    # --eps-snapshot: record weekly forwardEps snapshots for the same list.
    # Designed for a Sunday-evening cron so COMPASS has a fresh EPS revision
    # factor before the new trading week opens.
    if getattr(args, "eps_snapshot", False):
        print("\nrecording EPS snapshots …")
        from ..eps_tracker import batch_record_snapshots
        eps_results = batch_record_snapshots(symbols)
        recorded = sum(1 for v in eps_results.values() if v is not None)
        skipped = len(eps_results) - recorded
        print(f"  {recorded} snapshots recorded, {skipped} skipped (ETF / no coverage)")


if __name__ == "__main__":
    main()
