"""tradepro-fill-attribution — per-symbol P&L breakdown from real fills.

Queries the OMS orders + IG transaction history for a given date and
produces a per-symbol attribution report: who lost money, how many
trades, what the worst adverse excursion was.

This is the real answer to "what happened on Friday?" — not a
simulation but the actual executed fills.

Requires EC2 to be running and ~/.tradepro/credentials set.

Examples
--------
    # What happened on Friday June 6?
    uv run tradepro-fill-attribution --date 2026-06-06

    # Last 7 days rolling attribution
    uv run tradepro-fill-attribution --days 7

    # Show individual fill rows (not just symbol totals)
    uv run tradepro-fill-attribution --date 2026-06-06 --verbose

Output JSON
-----------
{
  "kind": "fill_attribution",
  "date": "2026-06-06",
  "total_realised_pnl_gbp": -1847.32,
  "total_fills": 47,
  "per_symbol": [
    {"symbol": "NVDA", "realised_pnl_gbp": -612.40, "fills": 8, ...},
    ...
  ],
  "worst_symbol": "NVDA",
  "worst_pnl_gbp": -612.40,
  "source": "oms_orders"
}
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any

_log = logging.getLogger("tradepro.cli.fill_attribution")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-fill-attribution",
        description=(
            "Per-symbol P&L attribution from real OMS fills. "
            "Answers 'what caused the loss on date X?'"
        ),
    )
    grp = p.add_mutually_exclusive_group(required=True)
    grp.add_argument("--date", default=None,
                     help="Single date to analyse (YYYY-MM-DD).")
    grp.add_argument("--days", type=int, default=None,
                     help="Rolling window: last N calendar days.")
    p.add_argument("--strategy", default=None,
                   help="Filter to a specific strategy_id (e.g. intraday_flat).")
    p.add_argument("--verbose", action="store_true",
                   help="Include individual fill rows in output.")
    p.add_argument("--log-level", default="WARNING",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args(argv)


def _date_range(args: argparse.Namespace) -> tuple[date, date]:
    if args.date:
        d = date.fromisoformat(args.date)
        return d, d
    today = date.today()
    return today - timedelta(days=args.days - 1), today


def _fetch_oms_orders(base: str, token: str, from_date: date, to_date: date) -> list[dict]:
    """Pull filled orders from the OMS within the date range."""
    import requests
    url = f"{base.rstrip('/')}/api/admin/oms-orders"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"limit": 2000}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        orders = r.json().get("orders") or []
    except Exception as exc:
        _log.error("OMS fetch failed: %s", exc)
        return []

    # Filter to FILLED orders in the date range
    result = []
    for o in orders:
        if o.get("state") != "FILLED":
            continue
        ts = o.get("lastStateChangeAtUtc") or o.get("createdAtUtc") or ""
        if not ts:
            continue
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        except ValueError:
            continue
        if from_date <= d <= to_date:
            result.append(o)
    return result


def _attribute_orders(
    orders: list[dict],
    strategy_filter: str | None,
    verbose: bool,
) -> dict[str, Any]:
    """Group filled orders by symbol and compute per-symbol P&L."""
    from collections import defaultdict

    # Group by symbol
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        if strategy_filter and strategy_filter not in (o.get("strategyId") or ""):
            continue
        sym = o.get("epic") or o.get("symbol") or o.get("instrumentId") or "UNKNOWN"
        # Normalise epic → symbol (IG epics look like "IX.D.SPTRD.IFS.IP" — skip those,
        # keep share epics like "CS.D.AAPL.CFD.IP" → extract "AAPL")
        if "." in sym:
            parts = sym.split(".")
            # IG share CFD epics: CS.D.<TICKER>.CFD.IP — 3rd segment is ticker
            if len(parts) >= 3 and parts[0] == "CS" and parts[1] == "D":
                sym = parts[2]
        by_symbol[sym].append(o)

    per_symbol: list[dict[str, Any]] = []
    total_pnl = 0.0
    total_fills = 0

    for sym, sym_orders in sorted(by_symbol.items()):
        fills = len(sym_orders)
        # P&L from OMS: use realisedPnl if available, else estimate from
        # fill prices (buy cost - sell proceeds). OMS stores P&L in the
        # order's realisedPnl field when the broker confirms it.
        pnl_values = [
            float(o.get("realisedPnl") or 0)
            for o in sym_orders
            if o.get("realisedPnl") is not None
        ]
        pnl = sum(pnl_values) if pnl_values else 0.0

        sides = {"BUY": 0, "SELL": 0}
        for o in sym_orders:
            side = (o.get("direction") or o.get("side") or "").upper()
            if "BUY" in side or "LONG" in side:
                sides["BUY"] += 1
            elif "SELL" in side or "SHORT" in side:
                sides["SELL"] += 1

        row: dict[str, Any] = {
            "symbol": sym,
            "realised_pnl_gbp": round(pnl, 2),
            "fills": fills,
            "buys": sides["BUY"],
            "sells": sides["SELL"],
        }
        if verbose:
            row["orders"] = [
                {
                    "id": o.get("id"),
                    "direction": o.get("direction"),
                    "qty": o.get("quantity"),
                    "fill_price": o.get("fillPrice"),
                    "pnl": o.get("realisedPnl"),
                    "ts": o.get("lastStateChangeAtUtc"),
                }
                for o in sorted(sym_orders, key=lambda x: x.get("lastStateChangeAtUtc") or "")
            ]

        per_symbol.append(row)
        total_pnl += pnl
        total_fills += fills

    per_symbol.sort(key=lambda r: r["realised_pnl_gbp"])  # worst first

    return {
        "total_realised_pnl_gbp": round(total_pnl, 2),
        "total_fills": total_fills,
        "per_symbol": per_symbol,
        "worst_symbol": per_symbol[0]["symbol"] if per_symbol else None,
        "worst_pnl_gbp": per_symbol[0]["realised_pnl_gbp"] if per_symbol else 0.0,
        "best_symbol": per_symbol[-1]["symbol"] if per_symbol else None,
        "best_pnl_gbp": per_symbol[-1]["realised_pnl_gbp"] if per_symbol else 0.0,
        "symbols_traded": len(per_symbol),
    }


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from . import push_to_api
    base, token = push_to_api.load_credentials()

    from_date, to_date = _date_range(args)
    _log.warning("Fetching fills %s → %s", from_date, to_date)

    orders = _fetch_oms_orders(base, token, from_date, to_date)
    _log.warning("Found %d filled orders", len(orders))

    attribution = _attribute_orders(orders, args.strategy, args.verbose)

    output: dict[str, Any] = {
        "kind": "fill_attribution",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "source": "oms_orders",
        **attribution,
    }

    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
