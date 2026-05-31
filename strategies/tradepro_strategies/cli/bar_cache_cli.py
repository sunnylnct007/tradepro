"""tradepro-bar-cache-get — operator CLI for the trustworthy bar cache.

Pull bars for one (canonical, asset_class, resolution) tuple over a
date range, populating the cache as a side effect. The first concrete
artefact of Phase B-1 — proves the architecture end-to-end against
yfinance without any backend / UI dependency.

Usage:

  uv run tradepro-bar-cache-get \\
      --canonical SPY --asset us_etf --resolution 1m \\
      --from 2024-12-23 --to 2024-12-31

  uv run tradepro-bar-cache-get \\
      --canonical SPY --asset us_etf --resolution 1d \\
      --from 2020-01-01 --to today

Exit codes:
  0  complete coverage, all partitions cached + manifest-validated
  1  partial coverage (gaps in the cache; --allow-partial was set)
  2  fatal error (no provider, schema mismatch, manifest violation)

Output is one line of summary per call + the on-disk paths to the
partitions (so the operator can ls / cat / spot-check). Verbose flag
prints the per-partition decisions.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradepro_strategies.bar_cache import (
    BarFetchError,
    BarStore,
    PreferencesLoader,
)
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers
from tradepro_strategies.bar_cache.providers import YFinanceProvider  # noqa: F401 — registers
from tradepro_strategies.bar_cache.telemetry import (
    BackendTelemetrySink,
    TelemetrySink,
)


_DEFAULT_BASE_DIR = Path.home() / ".tradepro" / "bar_cache"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch + cache bars for a (canonical, asset_class, "
            "resolution) tuple. First concrete artefact of Phase B-1."
        ),
    )
    parser.add_argument("--canonical", required=True,
                        help='Symbol (e.g. "SPY")')
    parser.add_argument("--asset", required=True,
                        help='Asset class (e.g. "us_etf")')
    parser.add_argument("--resolution", required=True,
                        help='Bar resolution (1m / 5m / 15m / 30m / 1h / 1d)')
    parser.add_argument("--from", dest="from_date", required=True,
                        help='Start date YYYY-MM-DD')
    parser.add_argument("--to", dest="to_date", default="today",
                        help='End date YYYY-MM-DD or "today"')
    parser.add_argument("--base-dir", default=str(_DEFAULT_BASE_DIR),
                        help=f"Cache base directory (default: {_DEFAULT_BASE_DIR})")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Don't fail on gaps; return what we have")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Ignore cache; re-fetch every partition")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Per-partition decision log")
    parser.add_argument(
        "--api-base", default=None,
        help=(
            "Optional API base URL (e.g. http://localhost:5252). When "
            "supplied, telemetry events also POST to "
            "/api/admin/data-trust/bar-cache/events so the cockpit's "
            "Bar cache activity panel sees them. JSONL fallback is "
            "always written too. Without --api-base, telemetry is "
            "JSONL-only — the cockpit will not see this run."
        ),
    )
    parser.add_argument(
        "--auth-token", default=None,
        help=(
            "Bearer token for the telemetry POST. Falls back to the "
            "TRADEPRO_API_TOKEN env var. Required only when the API "
            "demands auth (e.g. live deployments)."
        ),
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    try:
        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date)
    except ValueError as exc:
        print(f"date parse error: {exc}", file=sys.stderr)
        return 2

    base_dir = Path(args.base_dir).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.api_base:
        token = args.auth_token or os.environ.get("TRADEPRO_API_TOKEN")
        telemetry: TelemetrySink = BackendTelemetrySink(
            base_dir=base_dir,
            api_base=args.api_base,
            auth_token=token,
        )
        # Phase B-3 — the provider chain now reads from the
        # data_source_preferences table the cockpit edits. The CLI
        # only wires the loader when --api-base is set; without it
        # the BarStore stays on the hardcoded default (matching
        # Phase B-2 behaviour for offline-mode operators).
        preferences_loader = PreferencesLoader(
            api_base=args.api_base,
            auth_token=token,
        )
    else:
        telemetry = TelemetrySink(base_dir=base_dir)
        preferences_loader = None

    store = BarStore(
        base_dir=base_dir,
        telemetry=telemetry,
        preferences_loader=preferences_loader,
    )

    try:
        result = store.get(
            canonical=args.canonical,
            asset_class=args.asset,
            resolution=args.resolution,
            start=start,
            end=end,
            allow_partial=args.allow_partial,
            force_refresh=args.force_refresh,
            fetched_by=os.environ.get("USER", "cli"),
        )
    except BarFetchError as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
        print(f"  error_class    : {exc.error_class}", file=sys.stderr)
        print(f"  retry_strategy : {exc.retry_strategy}", file=sys.stderr)
        print(f"  expected       : {exc.expected}", file=sys.stderr)
        print(f"  actual         : {exc.actual}", file=sys.stderr)
        return 2

    # Summary one-liner + per-partition path.
    print(
        f"{args.canonical}/{args.asset}/{args.resolution} "
        f"{start.date()} → {end.date()}: "
        f"{result.rows_returned} of {result.rows_expected} bars "
        f"({'COMPLETE' if result.coverage_complete else 'PARTIAL'}) "
        f"via {result.provider_used}; "
        f"chain={result.provider_chain_tried}"
    )

    if args.verbose:
        print(f"  base_dir : {base_dir}")
        for partition in result.partitions_used:
            pq_path = (
                base_dir / args.asset / args.canonical / args.resolution
                / f"{partition}.parquet"
            )
            mf_path = (
                base_dir / args.asset / args.canonical / args.resolution
                / f"{partition}.manifest.json"
            )
            print(f"  partition {partition}:")
            print(f"    parquet  : {pq_path} ({pq_path.stat().st_size if pq_path.exists() else 0} bytes)")
            print(f"    manifest : {mf_path}")

    if not result.coverage_complete:
        return 1
    return 0


def _parse_date(s: str) -> datetime:
    """YYYY-MM-DD or "today"; tz-aware UTC midnight."""
    if s == "today":
        return datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"expected YYYY-MM-DD or 'today', got {s!r}: {exc}"
        )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
