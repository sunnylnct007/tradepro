"""tradepro-slippage-sweep — sensitivity sweep across slippage_bps.

Re-runs a backtest at multiple ``slippage_bps`` rungs and prints a
break-even table. Answers L2 directly: "would my strategy survive
at a wider spread?"

Examples:
    # Default rungs (1, 5, 10, 25, 50 bps)
    uv run tradepro-slippage-sweep --symbol SPY \\
        --from 2026-04-01 --to 2026-04-30

    # Custom rungs for thin single-name probe
    uv run tradepro-slippage-sweep --symbol XYZ \\
        --from 2026-04-01 --to 2026-04-30 \\
        --bps 10,20,40,80,150

    # Push to the cockpit
    uv run tradepro-slippage-sweep --symbol SPY \\
        --from 2026-04-01 --to 2026-04-30 --push

First rung populates the BarStore cache; subsequent rungs are
near-instant.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date

from ..paper import RiskLimits
from ..paper import registry as strategy_registry
from ..paper.slippage_sweep import (
    DEFAULT_BPS_RUNGS,
    format_table,
    run_sweep,
)
from ..paper.strategies import OpeningRangeBreakout  # ensures decorator runs
from . import push_to_api


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-slippage-sweep",
        description=(
            "Slippage sensitivity sweep: re-run the same backtest at "
            "multiple bps rungs, surface the break-even level."
        ),
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--strategy", default="orb",
                   help="Registered strategy name (see tradepro-paper-backtest --list-strategies)")
    p.add_argument("--strategy-class", default=None,
                   help="Dynamic import path 'module:ClassName' for ad-hoc strategies")
    p.add_argument("--param", action="append", default=[],
                   help="Strategy param override 'key=value' (repeatable)")
    p.add_argument("--strategy-id", default=None,
                   help="strategy_id stamped on orders / ledger")
    p.add_argument("--from", dest="from_date", required=True,
                   help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="to_date", required=True,
                   help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--bps", default=None,
                   help="Comma-separated slippage_bps rungs to sweep. "
                        f"Default: {','.join(str(b) for b in DEFAULT_BPS_RUNGS)}")
    p.add_argument("--capital-usd", type=float, default=100_000.0)
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0)
    p.add_argument("--range-minutes", type=int, default=15)
    p.add_argument("--risk-per-trade-usd", type=float, default=100.0)
    p.add_argument("--broker", default="yfinance",
                   choices=["yfinance", "replay"])
    p.add_argument("--interval", default="1m",
                   choices=["1m", "5m", "15m", "30m", "60m", "1h"])
    p.add_argument("--push", action="store_true",
                   help="POST the sweep JSON to the API as a paper-backtest "
                        "report (kind=slippage-sweep).")
    # Phase E preflight runs by DEFAULT. Asset class auto-resolves
    # from --symbol when --data-asset isn't given. The sweep is
    # single-symbol so the per-symbol hash IS the report hash.
    p.add_argument("--data-asset", default=None,
                   help="Asset class for the BarStore preflight. When "
                        "omitted, auto-resolves from --symbol "
                        "(AAPL→us_equity, SPY→us_etf, EURUSD=X→fx_spot, ...).")
    p.add_argument("--data-resolution", default=None,
                   help="Resolution for the preflight. Defaults to --interval "
                        "(1m sweep → 1m preflight, etc.).")
    p.add_argument("--data-api-base", default=None,
                   help="API base for telemetry + provider preferences.")
    p.add_argument("--allow-incomplete-data", action="store_true",
                   help="Override the Phase E hard-block. The report carries "
                        "coverage_complete=false so consumers can discount.")
    p.add_argument("--legacy-cache", action="store_true",
                   help="Opt-out of BarStore preflight (parity testing only).")
    return p.parse_args(argv)


def _parse_bps(raw: str | None) -> tuple[float, ...]:
    if not raw:
        return DEFAULT_BPS_RUNGS
    try:
        values = tuple(float(x.strip()) for x in raw.split(",") if x.strip())
    except ValueError as exc:
        raise SystemExit(f"--bps must be comma-separated numbers: {exc}")
    if not values:
        raise SystemExit("--bps cannot be empty")
    if any(v < 0 for v in values):
        raise SystemExit("--bps values must be >= 0")
    return values


def _parse_param_overrides(items: list[str]) -> dict:
    out: dict = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param expects key=value, got {item!r}")
        k, _, raw = item.partition("=")
        try:
            v = json.loads(raw)
        except json.JSONDecodeError:
            v = raw
        out[k.strip()] = v
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    bps_list = _parse_bps(args.bps)
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)

    spec = (
        strategy_registry.from_dotted(args.strategy_class)
        if args.strategy_class
        else strategy_registry.get(args.strategy)
    )
    base_params = spec.default_params()
    if "range_minutes" in base_params:
        base_params["range_minutes"] = args.range_minutes
    if "risk_per_trade_usd" in base_params:
        base_params["risk_per_trade_usd"] = args.risk_per_trade_usd
    base_params.update(_parse_param_overrides(args.param))

    strategy_id = args.strategy_id or args.strategy

    def make_strategy():
        return spec.build(
            strategy_id=strategy_id,
            params=dict(base_params),
            risk=RiskLimits(
                max_position_value_usd=args.max_position_value_usd,
                allow_short=False,
            ),
        )

    # Phase D-3 / E preflight (opt-in). Runs ONCE before the rungs —
    # the sweep re-runs the same data N times, so a single preflight
    # covers every rung's hash. Same exit-3 + structured-JSON
    # convention paper_backtest uses.
    preflight_payload: dict | None = None
    if not args.legacy_cache:
        from datetime import datetime as _dt, timezone as _tz
        from ..bar_cache import IncompleteDataError, preflight_data_state
        from ..bar_cache.asset_class_resolver import (
            ASSET_CLASS_UNKNOWN, resolve_asset_class,
        )
        resolved_asset = args.data_asset or resolve_asset_class(args.symbol)
        if resolved_asset == ASSET_CLASS_UNKNOWN:
            print(
                f"asset_class could not be resolved for symbol "
                f"{args.symbol!r}; pass --data-asset explicitly or "
                f"--legacy-cache to skip preflight.",
                file=sys.stderr,
            )
            return 4
        try:
            pf = preflight_data_state(
                canonical=args.symbol,
                asset_class=resolved_asset,
                resolution=args.data_resolution or args.interval,
                start=_dt.combine(start, _dt.min.time()).replace(tzinfo=_tz.utc),
                end=_dt.combine(end, _dt.min.time()).replace(tzinfo=_tz.utc),
                require_complete=not args.allow_incomplete_data,
                api_base=args.data_api_base,
                fetched_by=f"slippage-sweep:{spec.name}",
            )
        except IncompleteDataError as exc:
            print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
            return 3
        preflight_payload = pf.to_report_dict()
        preflight_payload.setdefault("data_state", {})["resolved_asset_class"] = (
            resolved_asset
        )
        preflight_payload["data_state"]["resolved_by"] = (
            "explicit" if args.data_asset else "auto-resolver"
        )

    print(
        f"Sweeping {args.symbol} {spec.name} {start}..{end} across "
        f"bps={list(bps_list)} — first rung populates cache.",
        file=sys.stderr,
    )

    result = asyncio.run(
        run_sweep(
            strategy_factory=make_strategy,
            symbol=args.symbol,
            start=start,
            end=end,
            bps_list=bps_list,
            capital_usd=args.capital_usd,
            broker=args.broker,
            interval=args.interval,
        )
    )

    print(format_table(result), file=sys.stderr)

    output = result.to_dict()
    if preflight_payload is not None:
        # Stamps top-level data_state_hash + nested data_state block
        # the same way paper_backtest does, so the D-2 cockpit chip
        # indexes sweep reports unchanged.
        output.update(preflight_payload)
    output["kind"] = "slippage-sweep"
    output["symbol"] = args.symbol
    output["strategy_id"] = strategy_id
    output["from"] = start.isoformat()
    output["to"] = end.isoformat()
    output["report_id"] = (
        f"slippage-sweep-{args.symbol}-{spec.name}-{start.isoformat()}-{end.isoformat()}"
    )
    print(json.dumps(output, indent=2, default=str))

    if args.push:
        base, token = push_to_api.load_credentials()
        push_to_api.push("paper-backtest", output, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
