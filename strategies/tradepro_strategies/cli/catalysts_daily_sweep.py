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
from ..catalysts_gdelt import fetch_all_catalysts as fetch_gdelt_catalysts
from ..catalysts_sec_edgar import fetch_all_catalysts as fetch_sec_catalysts
from ..catalysts_sink import (
    catalyst_to_upsert_body,
    extract_and_post,
    post_catalyst,
)
from ..catalysts_universe import (
    UniverseReport,
    assemble_universe,
    trader_large_50,
)
from ..news import fetch_news

GDELT_SOURCE = "gdelt"
SEC_SOURCE = "sec_edgar"

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
        "--gdelt", dest="gdelt", action="store_true", default=True,
        help="Include the free GDELT macro/event source (default ON). "
             "Fetches FOMC / CPI / OPEC / election / geopolitical "
             "catalysts and posts them on the relevant market proxy "
             "(SPY/XLE/GLD/...).",
    )
    p.add_argument(
        "--no-gdelt", dest="gdelt", action="store_false",
        help="Skip the GDELT macro/event source.",
    )
    p.add_argument(
        "--sec", dest="sec", action="store_true", default=True,
        help="Include the free, key-less SEC EDGAR filing source "
             "(default ON). Resolves each US-listed symbol to its CIK and "
             "posts 8-K material-event + 10-Q/10-K earnings filings within "
             "the lookback window on that symbol.",
    )
    p.add_argument(
        "--no-sec", dest="sec", action="store_false",
        help="Skip the SEC EDGAR filing source.",
    )
    p.add_argument(
        "--sec-days-back", type=int, default=30,
        help="SEC EDGAR filing lookback window in days (default 30).",
    )
    p.add_argument(
        "--push", action="store_true",
        help="Explicitly POST catalysts to the registry. This is the "
             "default behaviour unless --dry-run is set; the flag exists "
             "so the launchd command reads intentionally. No-op alongside "
             "the default; ignored when --dry-run is set.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the assembled universe + the catalyst bodies that "
             "WOULD be POSTed (incl. GDELT) and exit without POSTing.",
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

    # GDELT macro/event catalysts are market-wide, not per-ticker — fetch
    # them ONCE (not per universe symbol) and key by the proxy symbol the
    # event hits (SPY/XLE/GLD/...). Best-effort: a GDELT failure yields an
    # empty map and never blocks the per-symbol news sweep.
    gdelt_by_symbol: dict[str, list] = {}
    if args.gdelt:
        try:
            reports = fetch_gdelt_catalysts()
            gdelt_by_symbol = {
                sym: rep.all_catalysts
                for sym, rep in reports.items()
                if rep.all_catalysts
            }
            total = sum(len(v) for v in gdelt_by_symbol.values())
            _log.info(
                "GDELT macro source: %d catalysts across %d proxy symbols",
                total, len(gdelt_by_symbol),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open, never break the sweep
            _log.warning("GDELT source failed — continuing without it: %s", exc)
            gdelt_by_symbol = {}

    # SEC EDGAR filing catalysts are per-symbol (8-K material events +
    # 10-Q/10-K earnings) and land on the symbol that filed. Fetched across
    # the whole universe; symbols with no CIK (ETFs / FX / non-US filers)
    # are skipped silently. Best-effort: a SEC failure yields an empty map
    # and never blocks the rest of the sweep.
    sec_by_symbol: dict[str, list] = {}
    if args.sec:
        try:
            sec_reports = fetch_sec_catalysts(
                list(report.symbols), days_back=args.sec_days_back,
            )
            sec_by_symbol = {
                sym: rep.all_catalysts
                for sym, rep in sec_reports.items()
                if rep.all_catalysts
            }
            total = sum(len(v) for v in sec_by_symbol.values())
            _log.info(
                "SEC EDGAR source: %d catalysts across %d symbols",
                total, len(sec_by_symbol),
            )
        except Exception as exc:  # noqa: BLE001 — fail-open, never break the sweep
            _log.warning("SEC EDGAR source failed — continuing without it: %s", exc)
            sec_by_symbol = {}

    if args.dry_run:
        gdelt_dry = {
            sym: [
                catalyst_to_upsert_body(c, symbol=sym, source=GDELT_SOURCE)
                for c in cats
            ]
            for sym, cats in gdelt_by_symbol.items()
        }
        sec_dry = {
            sym: [
                catalyst_to_upsert_body(c, symbol=sym, source=SEC_SOURCE)
                for c in cats
            ]
            for sym, cats in sec_by_symbol.items()
        }
        print(json.dumps({
            "kind": "catalysts-daily-sweep",
            "dry_run": True,
            "universe": report.to_dict(),
            "gdelt_would_post": gdelt_dry,
            "gdelt_count": sum(len(v) for v in gdelt_dry.values()),
            "sec_would_post": sec_dry,
            "sec_count": sum(len(v) for v in sec_dry.values()),
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

    # ── GDELT macro/event catalysts → registry ───────────────────────────
    gdelt_rows: list[dict[str, Any]] = []
    gdelt_posted = 0
    gdelt_failed = 0
    for sym, cats in gdelt_by_symbol.items():
        for c in cats:
            ok = post_catalyst(
                c, symbol=sym, api_base=base, token=token, source=GDELT_SOURCE,
            )
            gdelt_rows.append({
                "symbol": sym,
                "kind": c.kind,
                "title": c.title,
                "occurs_on": c.occurs_on,
                "ok": ok,
            })
            extracted_total += 1
            if ok:
                gdelt_posted += 1
                posted_total += 1
            else:
                gdelt_failed += 1
                failed_total += 1

    # ── SEC EDGAR filing catalysts → registry ────────────────────────────
    sec_rows: list[dict[str, Any]] = []
    sec_posted = 0
    sec_failed = 0
    for sym, cats in sec_by_symbol.items():
        for c in cats:
            ok = post_catalyst(
                c, symbol=sym, api_base=base, token=token, source=SEC_SOURCE,
            )
            sec_rows.append({
                "symbol": sym,
                "kind": c.kind,
                "title": c.title,
                "occurs_on": c.occurs_on,
                "ok": ok,
            })
            extracted_total += 1
            if ok:
                sec_posted += 1
                posted_total += 1
            else:
                sec_failed += 1
                failed_total += 1

    summary = {
        "kind": "catalysts-daily-sweep",
        "universe": report.to_dict(),
        "per_symbol": per_symbol_reports,
        "gdelt": {
            "enabled": args.gdelt,
            "catalysts_posted": gdelt_posted,
            "catalysts_failed": gdelt_failed,
            "rows": gdelt_rows,
        },
        "sec": {
            "enabled": args.sec,
            "catalysts_posted": sec_posted,
            "catalysts_failed": sec_failed,
            "rows": sec_rows,
        },
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
