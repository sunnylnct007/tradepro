"""StrategyComparator — run N strategies over the same symbol +
date range, return a side-by-side scoreboard.

Why this exists: the question "which strategy should I deploy" is
always relative. Absolute P&L on ORB tells you nothing without a
baseline; ORB-vs-MeanReversion-vs-MomentumX on the same symbol over
the same window IS the comparison. This module is the runner that
produces that table.

Implementation: each strategy gets its own `WalkForwardValidator`
running independently. The cache-backed source chain means every
strategy after the first hits the parquet cache for its bars — so
comparing 5 strategies over 60 days costs roughly the same Yahoo
calls as the first strategy did.

The dashboard layer (.NET API endpoint + React page) calls this
function and serialises `ComparatorResult.to_summary()` straight to
the UI — no transformation needed.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from .validator import WalkForwardResult, WalkForwardValidator, StrategyFactory


log = logging.getLogger("tradepro.paper.comparator")


@dataclass
class ComparatorEntry:
    """One row in the comparison scoreboard."""
    strategy_id: str
    label: str
    result: WalkForwardResult

    def summary_row(self) -> dict:
        s = self.result.to_summary()
        s["label"] = self.label
        return s


@dataclass
class ComparatorResult:
    """The full side-by-side. `ranked_by_*` helpers exist so the UI
    can show top-3 by various metrics without re-computing on the
    client."""
    symbol: str
    start: date
    end: date
    entries: list[ComparatorEntry]

    def ranked_by_pnl(self) -> list[ComparatorEntry]:
        return sorted(self.entries, key=lambda e: e.result.total_realised_pnl, reverse=True)

    def ranked_by_sharpe(self) -> list[ComparatorEntry]:
        return sorted(self.entries, key=lambda e: e.result.sharpe_per_session, reverse=True)

    def ranked_by_drawdown(self) -> list[ComparatorEntry]:
        # Smaller is better — least painful drawdown ranks first
        return sorted(self.entries, key=lambda e: e.result.max_drawdown)

    def to_summary(self) -> dict:
        """JSON-friendly payload for the UI / API. Includes ALL
        entries' summary rows + the date range + a pre-sorted leaderboard
        keyed by metric so the frontend can render rankings without
        client-side sorting."""
        rows = [e.summary_row() for e in self.entries]
        return {
            "symbol": self.symbol,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "entries": rows,
            "rankings": {
                "by_total_pnl": [e.strategy_id for e in self.ranked_by_pnl()],
                "by_sharpe": [e.strategy_id for e in self.ranked_by_sharpe()],
                "by_drawdown": [e.strategy_id for e in self.ranked_by_drawdown()],
            },
        }


@dataclass
class ComparatorEntrySpec:
    """One input to the comparator. `factory()` returns a fresh
    Strategy instance; `label` is what the UI shows (operator-defined,
    not the registry name — so an operator can run "ORB 15-min" vs
    "ORB 30-min" as two entries with distinct labels)."""
    strategy_id: str
    label: str
    factory: StrategyFactory


@dataclass
class StrategyComparator:
    """Run a list of strategies over the same symbol/range and
    aggregate the results."""

    symbol: str
    capital_usd: float = 100_000.0
    broker: str = "yfinance"
    interval: str = "1m"
    slippage_bps: float = 5.0
    pace_seconds: float | str | None = None
    concurrent: bool = False
    """Run validators in parallel? Default False because the bar
    cache is the speed-up — sequential runs are already fast after
    the first warms the cache, and parallel runs all-miss-at-once
    on the FIRST run, hammering Yahoo. Flip to True only when you
    KNOW the cache is warm for the date range."""

    async def run(
        self,
        entries: Iterable[ComparatorEntrySpec],
        start: date | datetime,
        end: date | datetime,
    ) -> ComparatorResult:
        entries = list(entries)
        if not entries:
            raise ValueError("StrategyComparator.run: no entries")

        if self.concurrent:
            tasks = [self._run_one(e, start, end) for e in entries]
            results = await asyncio.gather(*tasks)
        else:
            results = []
            for e in entries:
                log.info("comparator running %s", e.label)
                results.append(await self._run_one(e, start, end))

        start_d = start.date() if isinstance(start, datetime) else start
        end_d = end.date() if isinstance(end, datetime) else end
        out_entries = [
            ComparatorEntry(strategy_id=e.strategy_id, label=e.label, result=r)
            for e, r in zip(entries, results)
        ]
        return ComparatorResult(
            symbol=self.symbol, start=start_d, end=end_d, entries=out_entries,
        )

    async def _run_one(
        self,
        entry: ComparatorEntrySpec,
        start: date | datetime,
        end: date | datetime,
    ) -> WalkForwardResult:
        validator = WalkForwardValidator(
            strategy_factory=entry.factory,
            symbol=self.symbol,
            capital_usd=self.capital_usd,
            broker=self.broker,
            interval=self.interval,
            slippage_bps=self.slippage_bps,
            pace_seconds=self.pace_seconds,
        )
        return await validator.run(start, end)


__all__ = [
    "StrategyComparator",
    "ComparatorEntrySpec",
    "ComparatorEntry",
    "ComparatorResult",
]
