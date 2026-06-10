"""tradepro-bar-cache-scorecard — data quality scorecard for the bar cache.

Reads all manifests in ~/.tradepro/bar_cache/ and produces an honest,
per-symbol, per-partition table showing:

  - How many bars are cached vs expected
  - Which data source wrote them (ibkr / ig / yfinance)
  - Quality tier (GOLD / SILVER / BRONZE / MISSING)
  - Explicit gap ranges so you know exactly what to (re-)fetch

Quality tiers:
  🥇 GOLD   — IBKR, ≥ 90 % of expected sessions covered
  🥈 SILVER — IBKR, < 90 % covered (partial month / end-of-history edge)
  🥉 BRONZE — yfinance or IG source (only 7-day 1m; not suitable for backtest)
  ✗  MISSING — no cached data at all for this partition

IBKR structural limits (hard limits from Interactive Brokers servers):
  1m  →  ~365 days back from today — older data unavailable without a paid vendor
  5m  →  ~3 years back
  1d  →  decades (essentially unlimited)

Usage:
    tradepro-bar-cache-scorecard
    tradepro-bar-cache-scorecard --resolution 1m --symbols "SPY,QQQ"
    tradepro-bar-cache-scorecard --json          # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_DEFAULT_BASE_DIR = Path.home() / ".tradepro" / "bar_cache"

# IBKR structural history limits per resolution
_IBKR_HISTORY_DAYS: dict[str, int | None] = {
    "1m":  365,
    "2m":  365,
    "5m":  1095,   # ~3 years
    "15m": 1095,
    "30m": 1095,
    "1h":  1825,   # ~5 years
    "1d":  None,   # essentially unlimited
    "1wk": None,
    "1mo": None,
}

# Months to look back when listing partitions (default view window)
_DEFAULT_LOOKBACK_MONTHS = 14


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tradepro-bar-cache-scorecard",
        description="Show data quality / coverage scorecard for the bar cache.",
    )
    parser.add_argument(
        "--base-dir", default=str(_DEFAULT_BASE_DIR),
        help=f"Cache root directory (default: {_DEFAULT_BASE_DIR})",
    )
    parser.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol filter (default: all symbols in cache)",
    )
    parser.add_argument(
        "--asset", default="us_etf",
        help="Asset class (default: us_etf)",
    )
    parser.add_argument(
        "--resolution", default="1m",
        choices=["1m", "2m", "5m", "15m", "30m", "1h", "1d"],
        help="Bar resolution (default: 1m)",
    )
    parser.add_argument(
        "--lookback-months", type=int, default=_DEFAULT_LOOKBACK_MONTHS,
        help=(
            f"How many months back to scan for partitions "
            f"(default: {_DEFAULT_LOOKBACK_MONTHS})"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Emit machine-readable JSON instead of the human table",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Include debug-level manifest details",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    base_dir = Path(args.base_dir).expanduser()
    if not base_dir.exists():
        print(f"✗  Cache directory not found: {base_dir}", file=sys.stderr)
        return 2

    # Filter symbols
    symbol_filter: set[str] | None = None
    if args.symbols:
        symbol_filter = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}

    rows = _collect_rows(
        base_dir=base_dir,
        asset=args.asset,
        resolution=args.resolution,
        lookback_months=args.lookback_months,
        symbol_filter=symbol_filter,
    )

    if not rows:
        print(
            f"No manifests found under {base_dir}/{args.asset}/*/{{res}}/\n"
            f"Run tradepro-bar-cache-harvest to populate the cache.",
        )
        return 1

    if args.json_output:
        print(json.dumps(rows, indent=2, default=str))
        return 0

    _print_table(rows, resolution=args.resolution)
    return 0


# ──────────────────────────────────────────────────────────────────────────────
# Data collection
# ──────────────────────────────────────────────────────────────────────────────

def _collect_rows(
    *,
    base_dir: Path,
    asset: str,
    resolution: str,
    lookback_months: int,
    symbol_filter: set[str] | None,
) -> list[dict[str, Any]]:
    """Scan manifests + detect missing partitions in the lookback window."""
    today_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    # Build list of YYYY-MM partition labels to check
    partitions_to_check = _partition_range(today_utc, lookback_months)

    asset_dir = base_dir / asset
    if not asset_dir.exists():
        return []

    # Discover symbols from directory structure
    symbols = sorted(
        d.name for d in asset_dir.iterdir()
        if d.is_dir() and (symbol_filter is None or d.name in symbol_filter)
    )

    ibkr_limit_days = _IBKR_HISTORY_DAYS.get(resolution)
    ibkr_earliest = (
        (today_utc - timedelta(days=ibkr_limit_days)).date()
        if ibkr_limit_days is not None else None
    )

    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        sym_res_dir = asset_dir / symbol / resolution
        for partition in partitions_to_check:
            manifest_path = sym_res_dir / f"{partition}.manifest.json"
            parquet_path  = sym_res_dir / f"{partition}.parquet"

            partition_dt = datetime.strptime(partition, "%Y-%m").replace(
                tzinfo=timezone.utc,
            )
            partition_age_days = (today_utc - partition_dt).days
            beyond_ibkr = (
                ibkr_limit_days is not None
                and partition_age_days > ibkr_limit_days + 31
            )

            if not manifest_path.exists():
                rows.append({
                    "symbol": symbol,
                    "resolution": resolution,
                    "partition": partition,
                    "status": "MISSING",
                    "tier": "missing",
                    "actual_bars": 0,
                    "expected_bars": None,
                    "actual_sessions": 0,
                    "expected_sessions": None,
                    "provider_used": None,
                    "fetched_at": None,
                    "beyond_ibkr_limit": beyond_ibkr,
                    "note": (
                        "beyond IBKR 1m limit — needs paid vendor"
                        if beyond_ibkr else
                        "not fetched — run harvest"
                    ),
                })
                continue

            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception as exc:  # noqa: BLE001
                rows.append({
                    "symbol": symbol,
                    "resolution": resolution,
                    "partition": partition,
                    "status": "CORRUPT",
                    "tier": "missing",
                    "actual_bars": 0,
                    "expected_bars": None,
                    "actual_sessions": 0,
                    "expected_sessions": None,
                    "provider_used": None,
                    "fetched_at": None,
                    "beyond_ibkr_limit": beyond_ibkr,
                    "note": f"manifest unreadable: {exc}",
                })
                continue

            actual_bars     = manifest.get("actual_bar_count", 0)
            expected_bars   = manifest.get("expected_bar_count", 0)
            actual_sessions = len(manifest.get("actual_session_dates", []))
            exp_sessions    = len(manifest.get("expected_session_dates", []))
            provider        = manifest.get("provider_used", "unknown")
            fetched_at      = manifest.get("fetched_at_utc")

            # Parquet sanity — manifest says rows but file might be wiped
            parquet_rows = _parquet_row_count(parquet_path)

            # If parquet says 0 rows but manifest says >0, flag as corrupt
            corrupt = parquet_rows == 0 and actual_bars > 0

            if corrupt:
                status = "CORRUPT"
                tier   = "missing"
            elif actual_bars == 0:
                status = "EMPTY"
                tier   = "missing"
            else:
                pct = actual_sessions / exp_sessions if exp_sessions else 1.0
                tier = _tier(provider, pct)
                if pct >= 0.90:
                    status = "COMPLETE"
                elif pct >= 0.50:
                    status = "PARTIAL"
                else:
                    status = "SPARSE"

            rows.append({
                "symbol": symbol,
                "resolution": resolution,
                "partition": partition,
                "status": status,
                "tier": tier,
                "actual_bars": actual_bars,
                "expected_bars": expected_bars,
                "actual_sessions": actual_sessions,
                "expected_sessions": exp_sessions,
                "provider_used": provider,
                "fetched_at": fetched_at,
                "beyond_ibkr_limit": beyond_ibkr,
                "note": "parquet 0 rows — manifest/file mismatch" if corrupt else "",
            })

    return rows


def _partition_range(today: datetime, lookback_months: int) -> list[str]:
    """Return YYYY-MM labels from lookback_months ago to today (inclusive)."""
    partitions = []
    year, month = today.year, today.month
    for _ in range(lookback_months + 1):
        partitions.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(partitions))


def _parquet_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        import pyarrow.parquet as pq
        return len(pq.read_table(path))
    except Exception:  # noqa: BLE001
        return -1  # unreadable


def _tier(provider: str, session_pct: float) -> str:
    p = (provider or "").lower()
    if p == "ibkr":
        return "gold" if session_pct >= 0.90 else "silver"
    return "bronze"


# ──────────────────────────────────────────────────────────────────────────────
# Table rendering
# ──────────────────────────────────────────────────────────────────────────────

_TIER_ICON = {
    "gold": "🥇",
    "silver": "🥈",
    "bronze": "🥉",
    "missing": "✗ ",
}
_STATUS_COLOUR = {
    "COMPLETE": "✓",
    "PARTIAL":  "~",
    "SPARSE":   "△",
    "EMPTY":    "○",
    "MISSING":  "–",
    "CORRUPT":  "!",
}


def _print_table(rows: list[dict[str, Any]], *, resolution: str) -> None:
    today_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    ibkr_limit_days = _IBKR_HISTORY_DAYS.get(resolution)
    ibkr_earliest = (
        (today_utc - timedelta(days=ibkr_limit_days)).date()
        if ibkr_limit_days else None
    )

    # ── Header ──────────────────────────────────────────────────
    now_str = today_utc.strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'─'*78}")
    print(f"  BAR CACHE DATA QUALITY SCORECARD   {now_str}")
    print(f"{'─'*78}")

    if ibkr_earliest:
        print(
            f"  IBKR {resolution} limit: ~{ibkr_limit_days}d "
            f"(earliest IBKR can fetch: {ibkr_earliest})\n"
            f"  Older partitions marked ⊘ BEYOND LIMIT — need paid vendor (Polygon.io etc)"
        )
    print()

    # ── Per-symbol blocks ────────────────────────────────────────
    symbols = sorted({r["symbol"] for r in rows})
    total_gold = total_silver = total_bronze = total_missing = 0

    for sym in symbols:
        sym_rows = [r for r in rows if r["symbol"] == sym]
        print(f"  {'─'*68}")
        print(f"  {sym}  /  {resolution}")
        print(f"  {'─'*68}")
        print(
            f"  {'PARTITION':<10}  {'STATUS':<9}  {'TIER':<8}  "
            f"{'BARS':>10}  {'SESSIONS':>10}  {'SOURCE':<12}  NOTE"
        )
        print(f"  {'─'*68}")

        for r in sym_rows:
            icon = _TIER_ICON.get(r["tier"], "? ")
            mark = _STATUS_COLOUR.get(r["status"], "?")
            bars_str = (
                f"{r['actual_bars']:,}/{r['expected_bars']:,}"
                if r["expected_bars"] is not None
                else "—"
            )
            sess_str = (
                f"{r['actual_sessions']}/{r['expected_sessions']}"
                if r["expected_sessions"] is not None
                else "—"
            )
            provider_str = r["provider_used"] or "—"
            note = ""
            if r.get("beyond_ibkr_limit") and r["status"] == "MISSING":
                note = "⊘ beyond IBKR limit"
            elif r.get("note"):
                note = r["note"]
            elif r["tier"] == "bronze":
                note = "⚠ yfinance: 7-day limit — not for backtest"

            print(
                f"  {r['partition']:<10}  "
                f"{mark} {r['status']:<8}  "
                f"{icon} {r['tier']:<6}  "
                f"{bars_str:>10}  {sess_str:>10}  "
                f"{provider_str:<12}  {note}"
            )
            # Tally
            t = r["tier"]
            if t == "gold":    total_gold    += 1
            elif t == "silver": total_silver += 1
            elif t == "bronze": total_bronze += 1
            else:               total_missing += 1

        # ── Per-symbol gap summary ───────────────────────────────
        missing = [r for r in sym_rows if r["status"] == "MISSING"]
        beyond  = [r for r in missing if r.get("beyond_ibkr_limit")]
        pendg   = [r for r in missing if not r.get("beyond_ibkr_limit")]
        bronze  = [r for r in sym_rows if r["tier"] == "bronze"]

        if pendg:
            first, last = pendg[0]["partition"], pendg[-1]["partition"]
            print(
                f"\n  ⏳ {len(pendg)} partition(s) PENDING IBKR fetch "
                f"({first} → {last})\n"
                f"     → run with TWS open: tradepro-bar-cache-harvest "
                f"--from {first}-01 --ibkr-only"
            )
        if beyond:
            print(
                f"\n  ⊘  {len(beyond)} partition(s) beyond IBKR {resolution} limit "
                f"({beyond[0]['partition']} → {beyond[-1]['partition']})\n"
                f"     → unavailable without a paid vendor (Polygon.io, Databento etc)"
            )
        if bronze:
            print(
                f"\n  ⚠  {len(bronze)} partition(s) have BRONZE (yfinance) data only.\n"
                f"     These cover the last ~7 days and are NOT suitable for backtesting.\n"
                f"     Replace with IBKR data: tradepro-bar-cache-harvest "
                f"--from {bronze[0]['partition']}-01 --ibkr-only --verbose"
            )
        print()

    # ── Overall summary ──────────────────────────────────────────
    total = total_gold + total_silver + total_bronze + total_missing
    print(f"{'─'*78}")
    print(
        f"  OVERALL  {total} partitions checked across {len(symbols)} symbol(s)\n"
        f"\n"
        f"  🥇 GOLD   {total_gold:4d}  (IBKR, ≥90% sessions — suitable for backtesting)\n"
        f"  🥈 SILVER {total_silver:4d}  (IBKR, <90% sessions — edge / partial month)\n"
        f"  🥉 BRONZE {total_bronze:4d}  (yfinance/IG — last 7 days only, NOT for backtest)\n"
        f"  ✗  MISSING {total_missing:3d}  (no data — fetch with IBKR or flag as unavailable)\n"
    )

    # ── Action list ──────────────────────────────────────────────
    print(f"{'─'*78}")
    print("  NEXT ACTIONS")
    print(f"{'─'*78}")

    if total_gold + total_silver == 0:
        print(
            "  1. Open TWS on port 7500 (IB Gateway paper — already running on DUP656969)\n"
            "  2. Run historical backfill (IBKR-only — no yfinance stubs):\n"
            "       cd strategies\n"
            f"       TRADEPRO_IBKR_PORT=7500 tradepro-bar-cache-harvest \\\n"
            f"         --from {(datetime.now(timezone.utc) - timedelta(days=365)).strftime('%Y-%m-%d')} \\\n"
            f"         --to {datetime.now(timezone.utc).strftime('%Y-%m-%d')} \\\n"
            f"         --resolution {resolution} --asset us_etf \\\n"
            "         --ibkr-only --allow-partial --verbose"
        )
    elif total_bronze > 0:
        print(
            "  ⚠  BRONZE partitions exist — yfinance stubs that should be upgraded:\n"
            "       TRADEPRO_IBKR_PORT=7500 tradepro-bar-cache-harvest \\\n"
            "         --ibkr-only --allow-partial --verbose\n"
            "     (force-refresh is NOT needed — IBKR data will overwrite BRONZE on re-fetch)"
        )
    else:
        print(
            "  ✓  Cache looks healthy. Run daily harvest after each session:\n"
            "       tradepro-bar-cache-harvest  (cron: com.tradepro.bar-cache-harvest)"
        )

    ibkr_earliest_str = str(ibkr_earliest) if ibkr_earliest else "N/A"
    print(
        f"\n  NOTE: {resolution} bars older than {ibkr_earliest_str} are STRUCTURALLY UNAVAILABLE\n"
        f"  from IBKR. No amount of re-fetching will recover them.\n"
        f"  For pre-{ibkr_earliest_str} {resolution} data you need a paid data vendor.\n"
    )
    print(f"{'─'*78}\n")


if __name__ == "__main__":
    sys.exit(main())
