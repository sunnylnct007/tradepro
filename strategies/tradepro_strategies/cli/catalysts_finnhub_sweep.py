"""tradepro-catalysts-finnhub — fetch Finnhub news + earnings calendar
and POST detected catalysts to the registry.

Runs across the trader's universe (same source as catalysts-daily-sweep)
but uses Finnhub instead of Yahoo Finance. Earnings dates are HIGH
confidence (from the exchange calendar). News items go through the
existing keyword extractor.

Designed to run every 2 hours from launchd (see the .plist).
Safe to re-run — the registry UPSERTs idempotently.

Requires TRADEPRO_FINNHUB_API_KEY env var (or AWS Secrets Manager
key "finnhub-api-key"). Exits 0 without posting if the key is absent
so the cron never fails noisily during setup.

Examples
--------
    # Full universe sweep
    uv run tradepro-catalysts-finnhub

    # Specific symbols only
    uv run tradepro-catalysts-finnhub --symbols "AAPL,MSFT,NVDA"

    # Dry-run — print what would be posted, no network POSTs
    uv run tradepro-catalysts-finnhub --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import push_to_api
from ..catalysts_finnhub import fetch_all_catalysts, _get_api_key
from ..catalysts_sink import post_catalyst, EXTRACTOR_SOURCE
from ..catalysts_universe import assemble_universe

_log = logging.getLogger("tradepro.cli.catalysts_finnhub_sweep")

FINNHUB_SOURCE = "finnhub"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-catalysts-finnhub",
        description="Fetch Finnhub news + earnings calendar and POST catalysts.",
    )
    p.add_argument("--symbols", default=None,
                   help="Comma-separated symbols. Defaults to full trader universe.")
    p.add_argument("--days-back", type=int, default=7,
                   help="Days of news history to fetch (default 7).")
    p.add_argument("--days-ahead", type=int, default=90,
                   help="Days ahead for earnings calendar (default 90).")
    p.add_argument("--dry-run", action="store_true",
                   help="Print catalysts; skip POST.")
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    api_key = _get_api_key()
    if not api_key:
        _log.warning(
            "TRADEPRO_FINNHUB_API_KEY not set — nothing to do. "
            "Set the env var or store the key in AWS Secrets Manager "
            "as 'finnhub-api-key'."
        )
        print(json.dumps({"kind": "catalysts-finnhub", "skipped": True,
                          "reason": "no API key"}, indent=2))
        return 0

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        report = assemble_universe()
        symbols = sorted(report.symbols)
        _log.info("Universe: %d symbols from %s", len(symbols), ", ".join(report.sources))

    if not args.dry_run:
        base, token = push_to_api.load_credentials()

    per_symbol: list[dict[str, Any]] = []
    total_posted = 0
    total_failed = 0

    for sym in symbols:
        cat_report = fetch_all_catalysts(
            sym,
            api_key=api_key,
            days_back=args.days_back,
            days_ahead=args.days_ahead,
        )
        if cat_report.skipped:
            continue

        rows: list[dict[str, Any]] = []
        for cat in cat_report.all_catalysts:
            if args.dry_run:
                rows.append({
                    "kind": cat.kind,
                    "title": cat.title,
                    "occurs_on": cat.occurs_on,
                    "confidence": cat.confidence,
                    "dry_run": True,
                })
            else:
                ok = post_catalyst(
                    cat,
                    symbol=sym,
                    api_base=base,
                    token=token,
                    source=FINNHUB_SOURCE,
                )
                rows.append({
                    "kind": cat.kind,
                    "title": cat.title[:80],
                    "occurs_on": cat.occurs_on,
                    "ok": ok,
                })
                if ok:
                    total_posted += 1
                else:
                    total_failed += 1

        per_symbol.append({
            "symbol": sym,
            "news_items": cat_report.news_items,
            "catalysts_from_news": cat_report.catalysts_from_news,
            "catalysts_from_earnings": cat_report.catalysts_from_earnings,
            "rows": rows,
        })

    summary = {
        "kind": "catalysts-finnhub",
        "dry_run": args.dry_run,
        "symbols_processed": len(per_symbol),
        "total_posted": total_posted,
        "total_failed": total_failed,
        "per_symbol": per_symbol,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
