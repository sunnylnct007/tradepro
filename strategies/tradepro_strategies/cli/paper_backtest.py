"""tradepro-paper-backtest — multi-session walk-forward backtest CLI.

Loops the paper engine across a date range, aggregates per-session
P&L into total/avg/Sharpe/drawdown stats, and prints the summary.

Examples:
    # Single point-in-time backtest (one historical session)
    uv run tradepro-paper-backtest --symbol AAPL --date 2026-04-15

    # 30-day walk-forward
    uv run tradepro-paper-backtest --symbol AAPL \\
        --from 2026-04-01 --to 2026-04-30

    # Custom ORB params
    uv run tradepro-paper-backtest --symbol AAPL \\
        --from 2026-04-01 --to 2026-04-30 \\
        --range-minutes 30 --risk-per-trade-usd 200

First run hits Yahoo for each session and writes parquet cache
underneath ~/.tradepro/cache/intraday/. Subsequent runs over the
same dates are near-instant — re-running a backtest with tweaked
params has no Yahoo cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime

from ..paper import RiskLimits
from ..paper import registry as strategy_registry
from ..paper.strategies import OpeningRangeBreakout  # ensures decorator runs
from ..paper.validator import WalkForwardValidator
from . import push_to_api


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-paper-backtest",
        description=(
            "Walk-forward backtest: run a strategy day-by-day across "
            "a date range, aggregate session P&L into summary stats."
        ),
    )
    p.add_argument("--symbol", required=False,
                   help="Symbol to trade (required unless --list-strategies)")
    p.add_argument("--strategy", default="orb",
                   help="Registered strategy name (see --list-strategies)")
    p.add_argument("--strategy-class", default=None,
                   help="Dynamic import path 'module:ClassName' for ad-hoc strategies "
                        "not registered via decorator or entry point")
    p.add_argument("--list-strategies", action="store_true",
                   help="Print all registered strategy names and exit")
    p.add_argument("--param", action="append", default=[],
                   help="Strategy param override 'key=value' (repeatable). "
                        "Strings/ints/floats/bools auto-coerced.")
    p.add_argument("--strategy-id", default=None,
                   help="strategy_id stamped on orders / ledger (defaults to --strategy)")
    # Date range OR single date. --date sets both --from and --to.
    p.add_argument("--from", dest="from_date", default=None,
                   help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="to_date", default=None,
                   help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--date", default=None,
                   help="Single-session shortcut: same date for both --from and --to")
    p.add_argument("--broker", default="yfinance",
                   choices=["yfinance", "replay"],
                   help="Bar source. Defaults to yfinance with parquet cache.")
    p.add_argument("--interval", default="1m",
                   choices=["1m", "5m", "15m", "30m", "60m", "1h"])
    p.add_argument("--capital-usd", type=float, default=100_000.0)
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0)
    p.add_argument("--risk-per-trade-usd", type=float, default=100.0)
    p.add_argument("--range-minutes", type=int, default=15)
    p.add_argument("--slippage-bps", type=float, default=5.0)
    p.add_argument("--include-sessions", action="store_true",
                   help="Include per-session details (otherwise summary only)")
    p.add_argument("--push", action="store_true",
                   help="POST the result JSON to the API as a paper-backtest report. "
                        "Reads ~/.tradepro/credentials for api_base_url + api_token.")
    # Phase D-3 + E: optional data-layer preflight. When --data-asset is
    # set the CLI calls BarStore.get for the full range BEFORE running
    # the backtest, capturing the data_state_hash for the report and
    # refusing the run on incomplete data (Phase E "stop pretending"
    # guarantee). Without --data-asset, behaviour is unchanged (legacy
    # cache.py path).
    p.add_argument("--data-asset", default=None,
                   help=("Asset class for the bar-cache preflight. When "
                         "omitted, the CLI auto-resolves from --symbol via "
                         "asset_class_resolver (AAPL→us_equity, SPY→us_etf, "
                         "EURUSD=X→fx_spot, ...). Pass an explicit value "
                         "to override the resolver."))
    p.add_argument("--legacy-cache", action="store_true",
                   help=("Opt-out of the BarStore preflight and use the "
                         "legacy cache.py path. Preserved for parity testing "
                         "and emergency rollback — every new caller should "
                         "leave this off so reports carry data_state_hash + "
                         "refuse on incomplete data."))
    p.add_argument("--data-resolution", default="1d",
                   help="Bar resolution for the preflight. Default 1d.")
    p.add_argument("--data-api-base", default=None,
                   help=("API base for telemetry + provider-preferences "
                         "lookup. Without it preflight uses local-only "
                         "telemetry + hardcoded yfinance chain."))
    p.add_argument("--allow-incomplete-data", action="store_true",
                   help=("Override Phase E hard-block. Preflight still "
                         "captures the data_state_hash but partial "
                         "coverage no longer refuses the run. The report "
                         "carries coverage_complete=False so consumers "
                         "can pivot."))
    return p.parse_args(argv)


def _parse_param_overrides(items: list[str]) -> dict:
    """Turn ['range_minutes=30', 'allow_short=true'] into a dict with
    auto-coerced types. Order of attempts: int, float, bool, str.
    Strategies that need richer types can JSON-encode the value."""
    import json
    out: dict = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--param expects key=value, got {item!r}")
        k, _, raw = item.partition("=")
        v: object
        try:
            v = json.loads(raw)
        except json.JSONDecodeError:
            v = raw  # bare string fallback
        out[k.strip()] = v
    return out


def _resolve_range(args: argparse.Namespace) -> tuple[date, date]:
    if args.date is not None:
        d = date.fromisoformat(args.date)
        return d, d
    if not args.from_date or not args.to_date:
        raise SystemExit("Provide either --date OR both --from and --to")
    return date.fromisoformat(args.from_date), date.fromisoformat(args.to_date)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_strategies:
        for name in strategy_registry.list_names():
            spec = strategy_registry.get(name)
            print(f"{name:<32} {spec.cls.__module__}:{spec.cls.__name__}")
        return 0
    if not args.symbol:
        raise SystemExit("--symbol is required (unless --list-strategies)")

    start, end = _resolve_range(args)

    # Resolve which strategy class to use. --strategy-class wins over
    # --strategy so an operator can override a registered name for a
    # one-off experiment without touching the registry.
    spec = (
        strategy_registry.from_dotted(args.strategy_class)
        if args.strategy_class
        else strategy_registry.get(args.strategy)
    )
    # Build the params dict from class defaults + CLI overrides. Range
    # minutes / risk per trade live here too — that way strategies
    # we add later get the same --param key=value plumbing for free.
    base_params = spec.default_params()
    # ORB-specific shorthand flags still work for back-compat.
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

    # Phase E — preflight runs by DEFAULT. Asset class auto-resolves
    # from the symbol when --data-asset isn't given (AAPL → us_equity,
    # SPY → us_etf, EURUSD=X → fx_spot, ...). Operator can override
    # the resolver with --data-asset, or skip preflight entirely with
    # --legacy-cache for parity testing / emergency rollback.
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
                resolution=args.data_resolution,
                start=_dt.combine(start, _dt.min.time()).replace(tzinfo=_tz.utc),
                end=_dt.combine(end, _dt.min.time()).replace(tzinfo=_tz.utc),
                require_complete=not args.allow_incomplete_data,
                api_base=args.data_api_base,
                fetched_by=f"paper-backtest:{spec.name}",
            )
        except IncompleteDataError as exc:
            print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
            return 3
        preflight_payload = pf.to_report_dict()
        # Stamp the resolved asset class on the payload so the cockpit
        # chip can show "auto-resolved as us_equity" rather than leaving
        # the operator guessing.
        preflight_payload.setdefault("data_state", {})["resolved_asset_class"] = (
            resolved_asset
        )
        preflight_payload["data_state"]["resolved_by"] = (
            "explicit" if args.data_asset else "auto-resolver"
        )

    validator = WalkForwardValidator(
        strategy_factory=make_strategy,
        symbol=args.symbol,
        capital_usd=args.capital_usd,
        broker=args.broker,
        interval=args.interval,
        slippage_bps=args.slippage_bps,
    )
    result = asyncio.run(validator.run(start, end))

    output = result.to_summary()
    if preflight_payload is not None:
        output.update(preflight_payload)
    if args.include_sessions:
        output["sessions"] = [
            {
                "date": s.session_date.isoformat(),
                "realised_pnl": round(s.realised_pnl, 4),
                "fills": s.fills_count,
                "commission": round(s.commission_paid, 4),
                "error": s.error,
            }
            for s in result.sessions
        ]
    output["kind"] = "backtest"
    output["report_id"] = (
        f"backtest-{args.symbol}-{spec.name}-{start.isoformat()}-{end.isoformat()}"
    )
    print(json.dumps(output, indent=2, default=str))
    if args.push:
        base, token = push_to_api.load_credentials()
        push_to_api.push("paper-backtest", output, base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
