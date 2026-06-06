"""tradepro-catalysts-daily-sweep — fire the C-2 extractor across
every symbol in the trader's effective universe.

Glue layer over the existing single-symbol ``tradepro-catalysts-extract``
that:

  1. Assembles the universe (positions + watchlists + trader's
     large_50 + baseline ETFs/FX) via
     ``tradepro_strategies.catalysts_universe.assemble_universe``.
  2. Calls ``extract_and_post`` for every symbol (sequentially —
     yfinance rate-limits cluster fast under burst, sequential is
     friendlier and the daily cron has no urgency).
  3. Logs a deterministic per-source breakdown so the launchd log
     is greppable for "which symbols got covered today".

Designed to run from launchd at 06:00 UTC daily. Cron-friendly: no
interactive prompts, structured stdout JSON, exit code reflects
hard failures only (per-symbol POST failures are counted but never
exit non-zero — the C-2 sink already swallows them).

Examples:
    # Default — trader large_50 + baseline ETFs + baseline FX
    uv run tradepro-catalysts-daily-sweep

    # With a positions list (operator pastes IBKR positions
    # until the .NET /api/positions/all endpoint is wired)
    uv run tradepro-catalysts-daily-sweep \\
        --positions "APLD,BABA,EC,MRVL,SWDA.L,VWRL.L"

    # Without the trader's quant universe (lean sweep)
    uv run tradepro-catalysts-daily-sweep --no-trader-universe

    # Dry-run prints the assembled universe and exits without POSTing
    uv run tradepro-catalysts-daily-sweep --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from . import push_to_api
from ..catalysts_sink import extract_and_post
from ..catalysts_universe import (
    UniverseReport,
    assemble_universe,
    trader_large_50,
)
from ..news import fetch_news

_log = logging.getLogger("tradepro.cli.catalysts_daily_sweep")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-catalysts-daily-sweep",
        description=(
            "Daily catalyst extractor sweep across the trader's "
            "effective universe (positions + watchlists + trader "
            "large_50 + baseline ETFs/FX). Idempotent on the registry "
            "side — safe to re-run; same headlines never duplicate."
        ),
    )
    p.add_argument(
        "--positions", default="",
        help="Comma-separated list of currently-held symbols. "
             "Highest priority in the universe. When the IBKR / T212 "
             "positions endpoint is wired the cron will pull from "
             "there automatically; for now accept manual input.",
    )
    p.add_argument(
        "--watchlists", default="",
        help="Comma-separated list of watched symbols.",
    )
    p.add_argument(
        "--extra", default="",
        help="Comma-separated list of extra symbols to add at the "
             "tail of the universe.",
    )
    p.add_argument(
        "--no-trader-universe", action="store_true",
        help="Skip the trader's large_50 + gold + benchmark.",
    )
    p.add_argument(
        "--no-baseline-etfs", action="store_true",
        help="Skip the baseline ETF list (SPY/QQQ/GLD/...).",
    )
    p.add_argument(
        "--no-baseline-fx", action="store_true",
        help="Skip the baseline FX pair list.",
    )
    p.add_argument(
        "--limit-per-symbol", type=int, default=10,
        help="Headlines per symbol to fetch (default 10).",
    )
    p.add_argument(
        "--max-age-days", type=int, default=14,
        help="Drop headlines older than N days (default 14).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the assembled universe and exit without POSTing.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args(argv)


def _csv(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Assemble the universe FIRST so a misconfigured run that
    # produces zero symbols dies before we spend cycles on news
    # fetches.
    trader_universe = None if args.no_trader_universe else trader_large_50()
    report: UniverseReport = assemble_universe(
        positions=_csv(args.positions),
        watchlists=_csv(args.watchlists),
        trader_universe=trader_universe,
        include_baseline_etfs=not args.no_baseline_etfs,
        include_baseline_fx=not args.no_baseline_fx,
        extra=_csv(args.extra),
    )

    if report.total == 0:
        _log.error(
            "assembled universe is empty — check --positions / --watchlists "
            "or disable the --no-* flags. Aborting.",
        )
        return 1

    _log.info(
        "assembled universe: %d symbols across %d sources",
        report.total, len(report.by_source),
    )
    for source, syms in report.by_source.items():
        _log.info("  %s (%d): %s",
                  source, len(syms),
                  ", ".join(syms[:8]) + ("..." if len(syms) > 8 else ""))

    if args.dry_run:
        print(json.dumps({
            "kind": "catalysts-daily-sweep",
            "dry_run": True,
            "universe": report.to_dict(),
        }, indent=2, default=str))
        return 0

    # Credentials resolved up-front so a missing token fails BEFORE
    # we burn yfinance fetches across the whole universe.
    base, token = push_to_api.load_credentials()

    per_symbol_reports: list[dict[str, Any]] = []
    posted_total = 0
    extracted_total = 0
    failed_total = 0
    for i, sym in enumerate(report.symbols, 1):
        if i % 10 == 0:
            _log.info("progress: %d/%d", i, report.total)
        headlines = fetch_news(
            sym, limit=args.limit_per_symbol, max_age_days=args.max_age_days,
        )
        result = extract_and_post(
            symbol=sym,
            news_items=headlines,
            api_base=base,
            token=token,
        )
        per_symbol_reports.append(result)
        posted_total += result.get("catalysts_posted", 0)
        extracted_total += result.get("catalysts_extracted", 0)
        failed_total += result.get("catalysts_failed", 0)

    summary = {
        "kind": "catalysts-daily-sweep",
        "universe": report.to_dict(),
        "per_symbol": per_symbol_reports,
        "totals": {
            "symbols_swept": report.total,
            "catalysts_extracted": extracted_total,
            "catalysts_posted": posted_total,
            "catalysts_failed": failed_total,
        },
    }
    print(json.dumps(summary, indent=2, default=str))
    _log.info(
        "sweep complete — extracted=%d posted=%d failed=%d across %d symbols",
        extracted_total, posted_total, failed_total, report.total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
