"""tradepro-paper — run one paper-trading session end-to-end.

Picks a broker profile (replay / yfinance / t212 / ibkr / stub_live),
instantiates the engine, registers one strategy, and prints the ledger
snapshot. Designed for "smoke a single session from the terminal".

Examples:
    # Backtest one ORB session against yfinance bars, sim fills
    uv run tradepro-paper --broker yfinance --symbol AAPL --date 2026-05-15

    # Same session, but route fills to the T212 demo account
    uv run tradepro-paper --broker t212 --symbol AAPL --date 2026-05-15

    # Live IBKR paper account (account starts with "DU")
    uv run tradepro-paper --broker ibkr --symbol AAPL --account DU1234567

T212 live trading requires both `--allow-real-orders` AND the env
var `TRADEPRO_T212_ALLOW_LIVE=1` — same two-key gate the router enforces.
IBKR live trading needs `TRADEPRO_IBKR_ALLOW_LIVE=1` and a non-DU
account id.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

from ..paper import RiskLimits
from ..paper.engine import Engine
from ..paper.profiles import build_multi_broker_session, build_session
from ..paper.strategies.opening_range_breakout import OpeningRangeBreakout


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-paper",
        description="Run one paper-trading session against a chosen broker.",
    )
    p.add_argument(
        "--broker", required=True,
        help=(
            "Broker profile. Single: replay | yfinance | t212 | ibkr | "
            "stub_live. Multi: comma-separated list (e.g. 't212,ibkr') — "
            "see --multi-mode and --bar-source."
        ),
    )
    p.add_argument(
        "--multi-mode", choices=["shadow", "dispatch"], default="shadow",
        help="Only used with a multi-broker --broker list. "
             "shadow=send every order to every broker; dispatch=route by strategy_id.",
    )
    p.add_argument(
        "--bar-source", choices=["yfinance", "ibkr", "replay"], default="yfinance",
        help="Bar feed used with multi-broker mode (single-broker mode derives this from --broker).",
    )
    p.add_argument("--symbol", required=True, help="Symbol to trade (e.g. AAPL)")
    p.add_argument(
        "--date", default=None,
        help="Session date YYYY-MM-DD (required for replay/yfinance/t212/stub_live)",
    )
    p.add_argument("--strategy-id", default="orb_default",
                   help="strategy_id stamped onto orders + ledger book")
    p.add_argument("--capital-usd", type=float, default=100_000.0,
                   help="Sub-account capital used by risk % checks")
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0,
                   help="Hard cap on |position_value| in dollars")
    p.add_argument("--risk-per-trade-usd", type=float, default=100.0,
                   help="Dollars risked on the stop for the strategy")
    p.add_argument("--range-minutes", type=int, default=15,
                   help="ORB opening-range window length")
    p.add_argument("--interval", default="1m",
                   help="Yfinance interval (1m/5m/15m/1h)")
    p.add_argument("--pace-seconds", default=None,
                   help="Replay pace: float seconds, 'realtime', or omit for as-fast-as-possible")
    # T212 knobs
    p.add_argument("--t212-mode", choices=["demo", "live"], default="demo")
    p.add_argument("--allow-real-orders", action="store_true",
                   help="Live trading opt-in (must also set the corresponding env var)")
    # IBKR knobs
    p.add_argument("--account", default=None,
                   help="IBKR account id (DU... = paper, U... = live)")
    p.add_argument("--ibkr-timeframe-seconds", type=int, default=60)
    return p.parse_args(argv)


def _resolve_session_date(arg: str | None) -> datetime | None:
    if arg is None:
        return None
    return datetime.fromisoformat(arg)


def _resolve_pace(arg: str | None) -> float | str | None:
    if arg is None:
        return None
    if arg == "realtime":
        return "realtime"
    return float(arg)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    session_date = _resolve_session_date(args.date)

    broker_list = [b.strip() for b in args.broker.split(",") if b.strip()]
    if len(broker_list) > 1:
        bus, router = build_multi_broker_session(
            brokers=broker_list,
            symbols=[args.symbol],
            mode=args.multi_mode,
            bar_source=args.bar_source,
            session_date=session_date,
            interval=args.interval,
            pace_seconds=_resolve_pace(args.pace_seconds),
            t212_mode=args.t212_mode,
            t212_allow_real_orders=args.allow_real_orders,
            ibkr_default_account=args.account,
            ibkr_allow_real_orders=args.allow_real_orders,
        )
    else:
        bus, router = build_session(
            broker=broker_list[0],
            symbols=[args.symbol],
            session_date=session_date,
            interval=args.interval,
            pace_seconds=_resolve_pace(args.pace_seconds),
            t212_mode=args.t212_mode,
            t212_allow_real_orders=args.allow_real_orders,
            ibkr_default_account=args.account,
            ibkr_allow_real_orders=args.allow_real_orders,
            ibkr_timeframe_seconds=args.ibkr_timeframe_seconds,
        )

    strategy = OpeningRangeBreakout(
        strategy_id=args.strategy_id,
        params={
            "range_minutes": args.range_minutes,
            "risk_per_trade_usd": args.risk_per_trade_usd,
        },
        risk=RiskLimits(
            max_position_value_usd=args.max_position_value_usd,
            allow_short=False,
        ),
    )

    engine = Engine(bus=bus, router=router)
    engine.register_strategy(
        strategy, symbols=[args.symbol], capital_usd=args.capital_usd,
    )

    snapshot = asyncio.run(engine.run(session_date or datetime.utcnow()))
    print(json.dumps(snapshot, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
