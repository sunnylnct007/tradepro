"""tradepro-bar-cache-harvest — batch harvest bars for the full universe.

Two modes in one command:

    # Daily cron (run from launchd at 21:15 UTC / 5:15 PM ET after close):
    tradepro-bar-cache-harvest
    # → uses IBKR primary; yfinance is acceptable fallback for today-only
    #   because TWS may briefly lag behind the cron schedule.

    # Historical backfill — IBKR-only (no yfinance stubs for old partitions):
    tradepro-bar-cache-harvest --from 2025-07-01 --to 2026-06-09 --ibkr-only
    # → TWS must be open on port 7497. If IBKR is unavailable the
    #   partition is skipped and reported as PENDING, not written with
    #   low-quality yfinance data.

    # Different resolution (daily bars = decades of history):
    tradepro-bar-cache-harvest --resolution 1d --from 2020-01-01

    # Override universe:
    tradepro-bar-cache-harvest --symbols "SPY,QQQ,NVDA"

IBKR history limits per resolution:
    1m  →  ~1 year back,  30-day request chunks
    5m  →  ~3 years back, 60-day request chunks
    1d  →  decades,       365-day request chunks

Data quality tiers (shown in scorecard):
    GOLD   — IBKR source, ≥90 % sessions covered
    SILVER — IBKR source, <90 % sessions covered (gaps / partial month)
    BRONZE — yfinance or IG source (acceptable for today-only; not for backtest)
    MISSING — no cached data at all

Provider precedence (always):
    ibkr → ig → yfinance
    --ibkr-only removes ig and yfinance from the chain so gaps stay explicit.

Exit codes:
    0  all symbols fully covered
    1  partial — some symbols have gaps (weekend/holiday gaps expected;
       IBKR market data farm outages produce this)
    2  fatal — every symbol failed
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tradepro_strategies.bar_cache import BarFetchError, BarStore, PreferencesLoader
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin  # noqa: F401 — registers
from tradepro_strategies.bar_cache.providers import YFinanceProvider  # noqa: F401 — registers
from tradepro_strategies.bar_cache.providers.ibkr_provider import IBKRProvider  # noqa: F401 — registers
from tradepro_strategies.bar_cache.telemetry import BackendTelemetrySink, TelemetrySink


# Full universe: intraday_flat candidates + SPY (regime filter).
# Keep in sync with intraday_flat.default_params()["candidates"].
_DEFAULT_SYMBOLS = [
    "SPY",                                            # regime filter
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",
    "META", "TSLA", "QQQ",  "AMD",  "NFLX", "AVGO",
]

_DEFAULT_BASE_DIR = Path.home() / ".tradepro" / "bar_cache"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="tradepro-bar-cache-harvest",
        description=(
            "Batch-harvest bars for the intraday_flat universe via IBKR TWS.\n\n"
            "Daily mode (default, no --from): harvest today's session.\n"
            "Backfill mode (explicit --from): harvest historical data up to\n"
            "IBKR's limit (~1 year of 1m bars, decades of daily bars)."
        ),
    )
    parser.add_argument(
        "--symbols", default=None,
        help=(
            "Comma-separated symbol list "
            "(default: SPY + full intraday_flat universe)"
        ),
    )
    parser.add_argument(
        "--universe", default=None,
        help=(
            "Comma-separated universe NAME(s) (e.g. 'large_50,high_beta') — "
            "harvest the SAME effective tickers the strategy desks trade, loaded "
            "live from /api/universes/<name>. This is how you harvest the FULL "
            "backtest universe instead of the hardcoded 12-symbol default. "
            "Takes precedence over --symbols. Requires the API to be reachable."
        ),
    )
    parser.add_argument(
        "--asset", default="us_etf",
        help="Asset class plugin (default: us_etf)",
    )
    parser.add_argument(
        "--resolution", default="1m",
        choices=["1m", "5m", "15m", "30m", "1h", "1d"],
        help="Bar resolution (default: 1m)",
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help=(
            "Start date YYYY-MM-DD. Omit for daily-cron mode (today only). "
            "Set to e.g. 2025-07-01 for a historical backfill."
        ),
    )
    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="End date YYYY-MM-DD inclusive (default: today)",
    )
    parser.add_argument(
        "--base-dir", default=str(_DEFAULT_BASE_DIR),
        help=f"Cache root directory (default: {_DEFAULT_BASE_DIR})",
    )
    parser.add_argument(
        "--allow-partial", action="store_true",
        help=(
            "Don't exit 1 on gaps — expected for weekend/holiday dates. "
            "Still prints which sessions were missing."
        ),
    )
    parser.add_argument(
        "--api-base", default=None,
        help=(
            "Optional API base URL (e.g. http://16.60.201.137). "
            "When set, telemetry events are POST-ed to EC2 so the cockpit "
            "Data Health panel reflects this harvest run."
        ),
    )
    parser.add_argument(
        "--auth-token", default=None,
        help="Bearer token for --api-base. Falls back to TRADEPRO_API_TOKEN env var.",
    )
    parser.add_argument(
        "--ibkr-only", action="store_true",
        help=(
            "Use IBKR as the ONLY provider — do not fall back to IG or yfinance. "
            "Required for historical backfill to prevent low-quality yfinance stubs "
            "(7-day 1m limit) being written into old partitions. "
            "Partitions that IBKR cannot serve are reported as PENDING rather than "
            "written with fallback data. TWS must be open on port 7497."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="INFO-level logging + per-partition paths")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # ── Symbols ────────────────────────────────────────────────
    # Precedence: --universe (the FULL strategy universe, live from the API) >
    # --symbols (explicit list) > the hardcoded 12-symbol intraday default. The
    # default exists only so a no-arg run still does something; credible
    # full-universe backtests need --universe.
    if args.universe:
        from tradepro_strategies.cli.paper_session import _fetch_universe_symbols
        symbols = []
        seen: set[str] = set()
        for uname in [u.strip() for u in args.universe.split(",") if u.strip()]:
            for s in _fetch_universe_symbols(uname):  # fail-loud on empty
                if s not in seen:
                    seen.add(s)
                    symbols.append(s)
        logging.getLogger("tradepro.harvest").info(
            "harvest universe %s → %d symbols", args.universe, len(symbols))
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = list(_DEFAULT_SYMBOLS)

    # ── Dates ─────────────────────────────────────────────────
    today_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    from_date = _parse_date(args.from_date) if args.from_date else today_utc
    # --to is inclusive; store uses half-open [start, end) so +1 day
    to_date = (
        (_parse_date(args.to_date) + timedelta(days=1))
        if args.to_date
        else (today_utc + timedelta(days=1))
    )

    # ── Store ──────────────────────────────────────────────────
    base_dir = Path(args.base_dir).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)

    if args.api_base:
        token = args.auth_token or os.environ.get("TRADEPRO_API_TOKEN")
        telemetry = BackendTelemetrySink(
            base_dir=base_dir, api_base=args.api_base, auth_token=token,
        )
        preferences_loader = PreferencesLoader(
            api_base=args.api_base, auth_token=token,
        )
    else:
        telemetry = TelemetrySink(base_dir=base_dir)
        preferences_loader = None

    # Provider chain depends on mode:
    # --ibkr-only (historical backfill): IBKR only — gaps stay explicit,
    #   no yfinance stubs for old partitions.
    # daily mode (no --ibkr-only): full chain — yfinance is an acceptable
    #   same-day fallback if TWS briefly lags.
    if args.ibkr_only:
        chain = ["ibkr"]
    else:
        chain = ["ibkr", "ig", "yfinance"]

    store = BarStore(
        base_dir=base_dir,
        telemetry=telemetry,
        preferences_loader=preferences_loader,
        provider_chain=chain,
    )

    # ── Header ─────────────────────────────────────────────────
    span_days = (to_date - from_date).days
    mode_label = (
        "daily" if span_days <= 1
        else f"backfill {span_days}d ({from_date.date()} → {(to_date - timedelta(days=1)).date()})"
    )
    chain_label = "ibkr-only" if args.ibkr_only else "ibkr→ig→yfinance"
    print(
        f"tradepro-bar-cache-harvest  mode={mode_label}  "
        f"res={args.resolution}  symbols={len(symbols)}  "
        f"chain={chain_label}"
    )
    if args.ibkr_only:
        print("  ⚡ IBKR-only mode: gaps will be reported as PENDING — no yfinance fallback")
    print("-" * 70)

    # ── Harvest loop ───────────────────────────────────────────
    ok_count = partial_count = fail_count = 0
    # Track quality tier counts: gold=ibkr complete, silver=ibkr partial,
    # bronze=yfinance/ig, missing=all failed
    quality_counts: dict[str, int] = {"gold": 0, "silver": 0, "bronze": 0, "missing": 0}
    # Per-symbol health records → POSTed to the cockpit's data-trust DB after the
    # run so the Harvest/Data-Health screen renders the real coverage (bridges
    # the local-cache → EC2 gap). Best-effort; never blocks the harvest.
    health_records: list[dict] = []

    for symbol in symbols:
        try:
            result = store.get(
                canonical=symbol,
                asset_class=args.asset,
                resolution=args.resolution,
                start=from_date,
                end=to_date,
                allow_partial=True,   # always read what's there; we report below
                fetched_by=os.environ.get("USER", "harvest"),
            )
            tier = _quality_tier(result.provider_used, result.coverage_complete)
            tier_icon = _tier_icon(tier)
            quality_counts[tier] += 1

            if result.coverage_complete:
                ok_count += 1
                mark = "✓"
            else:
                partial_count += 1
                mark = "~"

            print(
                f"  {mark} {symbol:<8s} "
                f"{result.rows_returned:6d}/{result.rows_expected:<6d} bars  "
                f"{tier_icon} {tier:<8s}  "
                f"source={result.provider_used}"
            )
            health_records.append({
                "canonical": symbol,
                "assetClass": args.asset,
                "lastFetchedResult": "ok" if result.coverage_complete else "partial",
                "lastFetchedProvider": result.provider_used,
                "lastFetchedResolution": args.resolution,
                "coverageStartDate": str(from_date)[:10],
                "coverageEndDate": str(to_date)[:10],
                "coveragePartitions": len(getattr(result, "partitions_used", []) or []),
                "missingDaysCount": max(0, int(result.rows_expected) - int(result.rows_returned)),
            })
        except BarFetchError as exc:
            fail_count += 1
            quality_counts["missing"] += 1
            # Be explicit: IBKR-only mode means "PENDING — open TWS to fill"
            if args.ibkr_only and "no_provider" in exc.error_class:
                print(f"  ⏳ {symbol:<8s} PENDING  — IBKR unavailable (TWS closed?)")
            else:
                print(
                    f"  ✗ {symbol:<8s} "
                    f"MISSING  — {exc.error_class}: {str(exc)[:60]}"
                )
        except Exception as exc:  # noqa: BLE001
            fail_count += 1
            quality_counts["missing"] += 1
            print(f"  ✗ {symbol:<8s} ERROR: {exc}")

    # ── Summary ────────────────────────────────────────────────
    print("-" * 70)
    print(
        f"Done: {ok_count} complete  "
        f"{partial_count} partial  "
        f"{fail_count} failed  "
        f"/ {len(symbols)} symbols"
    )
    print(
        f"Quality: "
        f"🥇 {quality_counts['gold']} GOLD  "
        f"🥈 {quality_counts['silver']} SILVER  "
        f"🥉 {quality_counts['bronze']} BRONZE  "
        f"✗ {quality_counts['missing']} MISSING"
    )
    if fail_count and args.ibkr_only:
        print(
            f"\n  ⏳ {fail_count} symbol(s) PENDING — open TWS on port 7497 and re-run:\n"
            f"     TRADEPRO_IBKR_PORT=7497 tradepro-bar-cache-harvest "
            f"--from {(from_date).date()} --to {(to_date - timedelta(days=1)).date()} "
            f"--ibkr-only --verbose"
        )
    elif fail_count:
        print(
            f"\n  ⚠  {fail_count} symbol(s) missing — run with --ibkr-only when TWS is open "
            f"to replace any BRONZE yfinance stubs with GOLD IBKR data."
        )

    if not args.allow_partial and partial_count:
        print(
            "  (partial gaps expected on non-trading days; "
            "use --allow-partial to suppress exit-1)"
        )

    # Report per-symbol health to the cockpit data-trust DB so the Harvest /
    # Data-Health screen renders the real coverage. Best-effort: any failure is
    # logged and ignored — it must never fail the harvest.
    if health_records:
        try:
            from tradepro_strategies.cli import push_to_api as _pta
            import requests as _rq
            _base, _tok = _pta.load_credentials()
            _h = {"Authorization": f"Bearer {_tok}"}
            _n = 0
            for _rec in health_records:
                _r = _rq.post(f"{_base.rstrip('/')}/api/admin/data-trust/bar-cache/health",
                              headers=_h, json=_rec, timeout=15)
                if _r.status_code in (200, 201):
                    _n += 1
            print(f"  ↑ reported health for {_n}/{len(health_records)} symbol(s) to the cockpit")
        except Exception as _exc:  # noqa: BLE001
            print(f"  (health report skipped: {_exc})")

    if fail_count == len(symbols):
        return 2
    if (partial_count or fail_count) and not args.allow_partial:
        return 1
    return 0


def _quality_tier(provider_used: str | None, complete: bool) -> str:
    """Map provider + completeness → quality tier label."""
    if not provider_used or provider_used == "none":
        return "missing"
    p = (provider_used or "").lower()
    if p == "ibkr":
        return "gold" if complete else "silver"
    # ig / yfinance / cache (derived from yf/ig)
    return "bronze"


def _tier_icon(tier: str) -> str:
    return {"gold": "🥇", "silver": "🥈", "bronze": "🥉", "missing": "✗ "}.get(tier, "?")


def _parse_date(s: str) -> datetime:
    """YYYY-MM-DD → tz-aware UTC midnight."""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    sys.exit(main())
