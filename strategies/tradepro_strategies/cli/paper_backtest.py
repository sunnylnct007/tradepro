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
    print(json.dumps(output, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
