"""CLI wrapper around the backtest engine. Example:

    uv run tradepro-backtest --symbol BARC.L --strategy sma_crossover \
        --from 2019-01-01 --to 2024-12-31 --capital 10000
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ..backtest import BacktestConfig, FeeModel, run_backtest
from ..cache import ensure_cached
from ..observability import RunLogger
from ..regimes import all_regime_stats
from ..strategies import available, resolve


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True)
    p.add_argument("--strategy", default="buy_and_hold", choices=available())
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

    logger = RunLogger()
    logger.emit(
        "cli.start",
        symbol=args.symbol, strategy=args.strategy, provider=args.provider,
        start=start, end=end, capital=args.capital,
    )

    prices = ensure_cached(args.provider, args.symbol, start, end)
    if prices.empty:
        print(f"no data for {args.symbol} on {args.provider}")
        logger.emit("cli.nodata")
        return

    signal_fn = resolve(args.strategy, {"fast": args.fast, "slow": args.slow})
    config = BacktestConfig(
        initial_capital=args.capital,
        currency=args.currency,
        fees=FeeModel(commission_per_trade=args.commission, stamp_duty_rate=args.stamp_duty),
    )
    result = run_backtest(prices, signal_fn, config)

    inputs = {
        "symbol": args.symbol,
        "strategy": args.strategy,
        "provider": args.provider,
        "from": args.start,
        "to": args.end,
        "capital": args.capital,
        "currency": args.currency,
        "fees": {"commission": args.commission, "stamp_duty": args.stamp_duty},
        "params": {"fast": args.fast, "slow": args.slow} if args.strategy == "sma_crossover" else {},
    }
    manifest = logger.write_manifest(inputs=inputs, stats=result.stats)

    # Per-regime breakdown — slices the equity curve over named historical
    # stress / recovery windows so the user can see which regimes hurt.
    regime_df = all_regime_stats(result.equity_curve)
    covered = regime_df[regime_df["bars"] > 0]

    # Persist artefacts next to the manifest.
    if not result.equity_curve.empty:
        result.equity_curve.to_frame().to_parquet(logger.artefact_dir / "equity_curve.parquet")
    if not result.trades.empty:
        result.trades.to_parquet(logger.artefact_dir / "trades.parquet")
    if not regime_df.empty:
        regime_df.to_parquet(logger.artefact_dir / "regimes.parquet")

    logger.emit("cli.done", bars=len(prices), trades=len(result.trades))

    print(f"run_id:     {logger.run_id}")
    print(f"symbol:     {args.symbol}")
    print(f"strategy:   {args.strategy}")
    print(f"bars:       {len(prices)}")
    print(f"trades:     {len(result.trades)}")
    for k, v in result.stats.items():
        print(f"{k:18s} {v:,.2f}")

    if not covered.empty:
        print()
        print(f"{'regime':28s} {'kind':10s} {'return %':>10s} {'max DD %':>10s}")
        for _, r in covered.iterrows():
            print(f"{r['regime_name']:28s} {r['kind']:10s} "
                  f"{r['return_pct']:>10.2f} {r['max_drawdown_pct']:>10.2f}")

    print(f"artefacts:  {logger.artefact_dir}")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **manifest,
            "trades": result.trades.to_dict(orient="records") if not result.trades.empty else [],
            "equity_curve": [
                {"timestamp": t.isoformat(), "equity": float(v)}
                for t, v in result.equity_curve.items()
            ],
            "regimes": regime_df.to_dict(orient="records") if not regime_df.empty else [],
        }
        args.out.write_text(json.dumps(payload, default=str, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
