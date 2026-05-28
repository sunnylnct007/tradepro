"""tradepro-regression-panel CLI — runs the frozen YAML against the
live compare cache. Wraps :mod:`tradepro_strategies.regression_panel`.

Usage::

    # Default: load tradepro_eval_regression.yaml at repo root, hit
    # the live API for each ticker's latest cached row, print a
    # markdown summary to stdout.
    uv run tradepro-regression-panel

    # Use a local snapshot of compare rows instead of the API
    # (useful for offline runs / CI).
    uv run tradepro-regression-panel --rows-from-file fixtures/rows.json

    # Write the report to a file instead of stdout.
    uv run tradepro-regression-panel --output report.md

Exit code is the number of FAILed cases — 0 means a clean run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger("tradepro.regression_panel.cli")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="tradepro-regression-panel")
    parser.add_argument(
        "--panel",
        default="tradepro_eval_regression.yaml",
        help="Path to the YAML panel (default: repo-root tradepro_eval_regression.yaml).",
    )
    parser.add_argument(
        "--rows-from-file",
        help="Load compare rows from a local JSON file keyed by ticker, "
             "instead of fetching from the live API. {ticker: row} format.",
    )
    parser.add_argument(
        "--output",
        help="Write the markdown report to this path. Default: stdout.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON instead of markdown.",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    from ..regression_panel import (
        evaluate_case,
        format_report,
        load_panel,
    )

    panel = load_panel(args.panel)
    cases = panel.get("cases") or []
    if not cases:
        print("panel has no cases", file=sys.stderr)
        return 2

    rows_by_ticker = _load_rows(args)

    results = []
    for case in cases:
        ticker = case.get("ticker")
        row = rows_by_ticker.get(ticker)
        result = evaluate_case(case, row)
        results.append(result)

    if args.json:
        payload = {
            "meta": panel.get("meta") or {},
            "results": [r.to_dict() for r in results],
        }
        output = json.dumps(payload, indent=2, default=str)
    else:
        output = format_report(results)

    if args.output:
        Path(args.output).write_text(output)
        log.info("wrote report to %s", args.output)
    else:
        print(output)

    # Exit code = count of failures so CI can fail-fast.
    return sum(1 for r in results if r.status == "fail")


def _load_rows(args) -> dict[str, dict]:
    """Return {ticker: compare_row}. From file when --rows-from-file is
    set; otherwise hit the API. Best-effort — missing rows show as
    "missing" in the report rather than crashing."""
    if args.rows_from_file:
        text = Path(args.rows_from_file).read_text()
        data = json.loads(text)
        # Accept either {ticker: row} or {"rows": [...]} shape.
        if isinstance(data, list):
            return {(r.get("symbol") or "").upper(): r for r in data if isinstance(r, dict)}
        return {k.upper(): v for k, v in data.items()}

    return _fetch_rows_from_api()


def _fetch_rows_from_api() -> dict[str, dict]:
    """Pull the latest compare rows from every cached universe and
    flatten into a {ticker_upper: row} map. Last-write-wins when a
    symbol is in multiple universes — we pick the row with the best
    rank (lowest int)."""
    try:
        import requests
    except ImportError:
        log.warning("requests not installed; no rows fetched")
        return {}
    try:
        from ..secrets import get_secret
        base = get_secret("api-base-url")
        token = get_secret("api-token")
    except Exception as e:  # noqa: BLE001
        log.warning("could not resolve api credentials: %s", e)
        return {}
    if not base or not token:
        log.warning("api-base-url or api-token missing; no rows fetched")
        return {}
    base = base.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}

    universes: list[str] = []
    try:
        r = requests.get(f"{base}/api/compare/universes", headers=headers, timeout=10)
        if r.ok:
            data = r.json()
            for u in (data.get("universes") or []):
                if isinstance(u, dict) and u.get("universe"):
                    universes.append(u["universe"])
                elif isinstance(u, str):
                    universes.append(u)
    except requests.RequestException as e:
        log.warning("universe list fetch failed: %s", e)
        return {}

    out: dict[str, dict] = {}
    for uname in universes:
        try:
            r = requests.get(
                f"{base}/api/compare/latest/{uname}",
                headers=headers, timeout=15,
            )
        except requests.RequestException as e:
            log.warning("fetch failed for universe %s: %s", uname, e)
            continue
        if not r.ok:
            continue
        payload = r.json().get("payload") or {}
        for row in payload.get("rows") or []:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            existing = out.get(sym)
            r_rank = row.get("rank") or 9999
            e_rank = (existing or {}).get("rank") or 9999
            if existing is None or r_rank < e_rank:
                out[sym] = row
    return out


if __name__ == "__main__":
    raise SystemExit(main())
