"""CLI wrapper around the backtest engine. Example:

    python scripts/run_backtest.py --symbol BARC.L --strategy sma_crossover \
        --from 2019-01-01 --to 2024-12-31 --capital 10000
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tradepro_strategies.backtest import BacktestConfig, FeeModel, run_backtest
from tradepro_strategies.data import DataRequest, load_candles
from tradepro_strategies.strategies import buy_and_hold_signals, sma_crossover_signals


STRATEGIES = {
    "buy_and_hold": lambda params: (lambda df: buy_and_hold_signals(df)),
    "sma_crossover": lambda params: (
        lambda df: sma_crossover_signals(df, fast=params.get("fast", 20), slow=params.get("slow", 50))
    ),
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--strategy", default="buy_and_hold", choices=sorted(STRATEGIES.keys()))
    p.add_argument("--provider", default="yahoo", choices=["yahoo", "stooq", "binance"])
    p.add_argument("--from", dest="start", default="2019-01-01")
    p.add_argument("--to", dest="end", default=datetime.utcnow().strftime("%Y-%m-%d"))
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--currency", default="GBP")
    p.add_argument("--stamp-duty", type=float, default=0.005)
    p.add_argument("--commission", type=float, default=0.0)
    p.add_argument("--fast", type=int, default=20)
    p.add_argument("--slow", type=int, default=50)
    p.add_argument("--out", type=Path, default=None, help="write result JSON to path")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)

    prices = load_candles(DataRequest(
        symbol=args.symbol, start=start, end=end, interval="1d", provider=args.provider,
    ))
    if prices.empty:
        print(f"no data for {args.symbol} on {args.provider}")
        return

    signal_fn = STRATEGIES[args.strategy]({"fast": args.fast, "slow": args.slow})
    config = BacktestConfig(
        initial_capital=args.capital,
        currency=args.currency,
        fees=FeeModel(commission_per_trade=args.commission, stamp_duty_rate=args.stamp_duty),
    )
    result = run_backtest(prices, signal_fn, config)

    print(f"symbol:    {args.symbol}")
    print(f"strategy:  {args.strategy}")
    print(f"bars:      {len(prices)}")
    print(f"trades:    {len(result.trades)}")
    for k, v in result.stats.items():
        print(f"{k:18s} {v:,.2f}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "symbol": args.symbol,
            "strategy": args.strategy,
            "currency": args.currency,
            "stats": result.stats,
            "trades": result.trades.to_dict(orient="records") if not result.trades.empty else [],
            "equity_curve": [
                {"timestamp": t.isoformat(), "equity": float(v)}
                for t, v in result.equity_curve.items()
            ],
        }
        args.out.write_text(json.dumps(payload, default=str, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
