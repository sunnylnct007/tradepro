"""tradepro-catalysts-extract — run the news extractor + POST hits.

Phase C-2 of the catalyst overlay. Fetches recent headlines for one
or more symbols, runs the pure-Python extractor over them, and POSTs
every detected dated catalyst to ``/api/catalysts/`` with
``source="extractor.news"``. The endpoint UPSERTs idempotently so
the same run repeated on the same news bundle never duplicates rows.

Examples
--------
    # One symbol
    uv run tradepro-catalysts-extract --symbol AAPL

    # Multi-symbol via comma-separated list
    uv run tradepro-catalysts-extract --symbols "AAPL,MSFT,GOOGL,NVDA"

    # Dry-run: extract + print but don't POST (helpful for sanity
    # check before hitting prod)
    uv run tradepro-catalysts-extract --symbol AAPL --dry-run

The extractor is keyword + regex based today (no external API call).
Yahoo Finance is the news source; missing headlines → empty result,
not an error. Designed to be safe to re-run on a cron — idempotent
on the endpoint side, no rate-limit risk on the cron side.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import push_to_api
from ..catalysts_sink import extract_and_post
from ..news import fetch_news


_log = logging.getLogger("tradepro.cli.catalysts_extract")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-catalysts-extract",
        description=(
            "Fetch news for one or more symbols, run the catalyst "
            "extractor, and POST every detected dated event to the "
            "registry (source=extractor.news). Safe to re-run — the "
            "endpoint UPSERTs idempotently."
        ),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--symbol", default=None,
        help="Single symbol to extract for (e.g. AAPL).",
    )
    src.add_argument(
        "--symbols", default=None,
        help="Comma-separated symbol list (e.g. 'AAPL,MSFT,GOOGL').",
    )
    p.add_argument(
        "--limit", type=int, default=10,
        help="Headlines per symbol to fetch (default 10).",
    )
    p.add_argument(
        "--max-age-days", type=int, default=14,
        help="Drop headlines older than N days before extraction (default 14).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Extract + print but do not POST. Useful for spot-checks.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args(argv)


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbol:
        return [args.symbol.strip().upper()]
    return [s.strip().upper() for s in args.symbols.split(",") if s.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    symbols = _resolve_symbols(args)
    if not symbols:
        raise SystemExit("no symbols resolved from --symbol / --symbols")

    # Resolve credentials up-front so a missing token fails BEFORE we
    # spend yfinance fetches. In dry-run mode skip — no POSTs happen.
    if not args.dry_run:
        base, token = push_to_api.load_credentials()
    else:
        base, token = "(dry-run)", "(dry-run)"

    per_symbol_reports: list[dict[str, Any]] = []
    for sym in symbols:
        _log.info("fetching news for %s (limit=%d, max_age=%dd)",
                  sym, args.limit, args.max_age_days)
        headlines = fetch_news(
            sym, limit=args.limit, max_age_days=args.max_age_days,
        )
        if args.dry_run:
            # Run the extractor locally without POSTing; report the
            # rows we WOULD have posted so the operator can spot-check.
            from ..catalysts_sink import catalyst_to_upsert_body
            from ..catalysts import extract_catalysts
            catalysts = extract_catalysts(headlines)
            per_symbol_reports.append({
                "symbol": sym,
                "dry_run": True,
                "headlines_seen": len(headlines),
                "catalysts_extracted": len(catalysts),
                "would_post": [
                    catalyst_to_upsert_body(c, symbol=sym)
                    for c in catalysts
                ],
            })
            continue

        report = extract_and_post(
            symbol=sym,
            news_items=headlines,
            api_base=base,
            token=token,
        )
        per_symbol_reports.append(report)

    summary = {
        "kind": "catalysts-extract",
        "symbols": symbols,
        "dry_run": args.dry_run,
        "per_symbol": per_symbol_reports,
        "totals": {
            "headlines_seen": sum(r.get("headlines_seen", 0) for r in per_symbol_reports),
            "catalysts_extracted": sum(r.get("catalysts_extracted", 0) for r in per_symbol_reports),
            "catalysts_posted": sum(r.get("catalysts_posted", 0) for r in per_symbol_reports),
            "catalysts_failed": sum(r.get("catalysts_failed", 0) for r in per_symbol_reports),
        },
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
