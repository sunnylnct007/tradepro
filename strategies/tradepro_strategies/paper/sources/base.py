"""BarSource ABC + a generic Bus that replays whatever a source
returns. Concrete sources (yfinance/finnhub/cache/fallback) live in
sibling modules so this file stays free of vendor coupling."""
from __future__ import annotations

import asyncio
import heapq
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..bar_bus import BarBus, ReplayBarBus
from ..strategy import Bar


class BarSource(ABC):
    """One method, one job. Given a symbol + a session date + an
    interval, return the bars covering that session in timestamp
    order. Sources that don't support a (symbol, date, interval)
    triple return an empty list — never raise — so the FallbackSource
    can keep walking the chain."""

    name: str = "source"

    @abstractmethod
    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        ...


@dataclass
class SourceBackedBus(BarBus):
    """Adapter that turns any `BarSource` into a `BarBus`.

    The bus calls `source.fetch(...)` once at the start of `run()`,
    then hands the bars to a `ReplayBarBus` for pacing + queue emission.
    Lets the rest of the engine stay source-agnostic.
    """

    source: BarSource = None  # type: ignore[assignment]
    symbol: str = ""
    session_date: datetime | None = None
    interval: str = "1m"
    pace_seconds: float | str | None = None
    name: str = "source_backed_bus"

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if self.source is None or not self.symbol or self.session_date is None:
            raise ValueError(
                "SourceBackedBus requires source + symbol + session_date"
            )
        bars = await self.source.fetch(self.symbol, self.session_date, self.interval)
        replay = ReplayBarBus(
            bars=bars, pace_seconds=self.pace_seconds, name=self.name,
        )
        await replay.run(out_queue, shutdown_queue)


@dataclass
class MultiSymbolSourceBackedBus(BarBus):
    """Multi-symbol variant of `SourceBackedBus`.

    Fetches every symbol concurrently via `asyncio.gather`, merges the
    per-symbol bar lists in timestamp order, and replays the merged
    stream through a single `ReplayBarBus`. The engine's per-strategy
    fanout already filters by `symbol in registration.symbols`, so one
    bus handles N symbols cleanly.

    Each per-symbol fetch is independent — a source that returns []
    for one symbol does not stall the others, matching the BarSource
    contract.
    """

    source: BarSource = None  # type: ignore[assignment]
    symbols: list[str] = field(default_factory=list)
    session_date: datetime | None = None
    interval: str = "1m"
    pace_seconds: float | str | None = None
    name: str = "multi_symbol_source_backed_bus"

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if self.source is None or not self.symbols or self.session_date is None:
            raise ValueError(
                "MultiSymbolSourceBackedBus requires source + symbols + session_date"
            )
        per_symbol_bars = await asyncio.gather(*[
            self.source.fetch(sym, self.session_date, self.interval)
            for sym in self.symbols
        ])
        # heapq.merge sorts by Bar.timestamp; each per-symbol list is
        # already in timestamp order from the source contract.
        merged = list(heapq.merge(*per_symbol_bars, key=lambda b: b.timestamp))
        replay = ReplayBarBus(
            bars=merged, pace_seconds=self.pace_seconds, name=self.name,
        )
        await replay.run(out_queue, shutdown_queue)


__all__ = ["BarSource", "SourceBackedBus", "MultiSymbolSourceBackedBus"]
