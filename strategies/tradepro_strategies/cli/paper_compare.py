"""tradepro-paper-compare — side-by-side backtest of N strategies.

Each `--entry` is `label::strategy_name[?param1=value1&param2=value2]`.
The same registered strategy can appear multiple times with different
param overrides — useful for tuning runs.

Examples:
    # Compare ORB at 15-min vs 30-min opening window
    uv run tradepro-paper-compare --symbol AAPL \\
        --from 2026-04-01 --to 2026-04-30 \\
        --entry "ORB-15::orb?range_minutes=15" \\
        --entry "ORB-30::orb?range_minutes=30"

    # Compare two registered strategies
    uv run tradepro-paper-compare --symbol AAPL \\
        --from 2026-04-01 --to 2026-04-30 \\
        --entry "Breakout::orb" \\
        --entry "MeanRev::my_pkg.strategies:MeanReversion"

Output is JSON: per-entry summary + leaderboards ranked by total P&L,
Sharpe, and max drawdown. Pipe through `jq` for ad-hoc filtering, or
feed straight to the dashboard endpoint (same schema).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from urllib.parse import parse_qs

from ..paper import RiskLimits
from ..paper import registry as strategy_registry
from ..paper.comparator import ComparatorEntrySpec, StrategyComparator
from ..paper.strategies import OpeningRangeBreakout  # ensures decorator runs


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="tradepro-paper-compare",
        description="Run N strategies over the same symbol/range and rank them.",
    )
    p.add_argument("--symbol", required=True)
    p.add_argument("--from", dest="from_date", required=True,
                   help="Start date YYYY-MM-DD (inclusive)")
    p.add_argument("--to", dest="to_date", required=True,
                   help="End date YYYY-MM-DD (inclusive)")
    p.add_argument("--entry", action="append", required=True,
                   help="Entry spec 'Label::name[?param=value&...]' "
                        "or 'Label::module:Class[?param=value]'. Repeatable.")
    p.add_argument("--capital-usd", type=float, default=100_000.0)
    p.add_argument("--max-position-value-usd", type=float, default=10_000.0)
    p.add_argument("--broker", default="yfinance", choices=["yfinance", "replay"])
    p.add_argument("--interval", default="1m",
                   choices=["1m", "5m", "15m", "30m", "60m", "1h"])
    p.add_argument("--concurrent", action="store_true",
                   help="Run validators concurrently. Only safe when the bar "
                        "cache is already warm; otherwise N×Yahoo cost.")
    return p.parse_args(argv)


def _build_entry_specs(
    raw_entries: list[str],
    max_position_value_usd: float,
) -> list[ComparatorEntrySpec]:
    out: list[ComparatorEntrySpec] = []
    seen_ids: set[str] = set()
    for raw in raw_entries:
        if "::" not in raw:
            raise SystemExit(
                f"--entry expects 'Label::strategy[?params]', got {raw!r}"
            )
        label, _, rest = raw.partition("::")
        name_part, _, query = rest.partition("?")
        params = _parse_query_params(query) if query else {}
        spec = (
            strategy_registry.from_dotted(name_part)
            if ":" in name_part
            else strategy_registry.get(name_part)
        )
        # Make a unique strategy_id per entry so the ledger doesn't
        # cross-contaminate when two entries use the same registered
        # name (e.g. "ORB-15" and "ORB-30" both come from "orb").
        sid_base = label.lower().replace(" ", "_")
        sid = sid_base
        n = 1
        while sid in seen_ids:
            n += 1
            sid = f"{sid_base}_{n}"
        seen_ids.add(sid)

        # default_params + CLI overrides, captured in a closure per
        # entry so each comparator run gets the right param dict.
        merged = {**spec.default_params(), **params}

        def make(spec=spec, sid=sid, merged=merged):
            return spec.build(
                strategy_id=sid,
                params=dict(merged),
                risk=RiskLimits(
                    max_position_value_usd=max_position_value_usd,
                    allow_short=False,
                ),
            )

        out.append(ComparatorEntrySpec(strategy_id=sid, label=label, factory=make))
    return out


def _parse_query_params(query: str) -> dict:
    """`range_minutes=30&allow_short=true` → coerced dict. Same
    JSON-decode-fallback-to-string logic as the backtest CLI's
    --param parser."""
    import json
    parsed = parse_qs(query, keep_blank_values=False)
    out: dict = {}
    for k, vs in parsed.items():
        raw = vs[-1]
        try:
            out[k] = json.loads(raw)
        except json.JSONDecodeError:
            out[k] = raw
    return out


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)

    entries = _build_entry_specs(args.entry, args.max_position_value_usd)

    comparator = StrategyComparator(
        symbol=args.symbol,
        capital_usd=args.capital_usd,
        broker=args.broker,
        interval=args.interval,
        concurrent=args.concurrent,
    )
    result = asyncio.run(comparator.run(entries, start, end))
    print(json.dumps(result.to_summary(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
