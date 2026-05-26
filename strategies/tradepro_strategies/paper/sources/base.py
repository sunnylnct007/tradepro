"""BarSource ABC + a generic Bus that replays whatever a source
returns. Concrete sources (yfinance/finnhub/cache/fallback) live in
sibling modules so this file stays free of vendor coupling."""
from __future__ import annotations

import asyncio
import heapq
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..bar_bus import BarBus, ReplayBarBus
from ..strategy import Bar


def _lookback_dates(session_date: datetime, lookback_days: int) -> list[datetime]:
    """Return candidate dates for a lookback window.

    Generates (lookback_days + 7) business days backward from session_date
    so bank holidays never leave the window empty. _fetch_window filters
    out empty fetch results and selects the most recent lookback_days
    non-empty days, so callers always get a full warmup window even if
    yesterday (or the past few days) were bank holidays.

    lookback_days=0 returns only session_date (preserves single-day behaviour).
    """
    if lookback_days < 0:
        raise ValueError("lookback_days must be >= 0")
    base = session_date.replace(hour=0, minute=0, second=0, microsecond=0)
    if lookback_days == 0:
        return [base]
    import pandas as pd
    # Buffer of 7 covers two consecutive bank-holiday weeks (e.g. Easter).
    n = lookback_days + 7
    # Go back far enough in calendar days; bdate_range handles Mon-Fri.
    start = base - timedelta(days=n * 3)
    bdates = pd.bdate_range(start=start, end=base, freq="B")
    # Keep the most recent n+1 business days (includes session_date if weekday).
    return [ts.to_pydatetime().replace(tzinfo=None) for ts in bdates[-(n + 1):]]


async def _fetch_window(
    source: "BarSource",
    symbol: str,
    session_date: datetime,
    interval: str,
    lookback_days: int,
) -> tuple[list[Bar], datetime | None]:
    """Fetch a warmup window for (symbol, session_date).

    Returns (bars, data_window_start) where data_window_start is the
    earliest date that contributed bars to the lookback window (None if
    lookback_days=0 or all lookback candidates were empty).

    Holiday behaviour: candidate dates include a business-day buffer, so
    when the most recent calendar days were bank holidays the window falls
    back to the nearest available trading day rather than returning empty.
    The final bar list always contains the most recent `lookback_days`
    non-empty days' bars + session_date bars, in timestamp order.
    """
    base = session_date.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = _lookback_dates(session_date, lookback_days)

    per_day = await asyncio.gather(*[source.fetch(symbol, d, interval) for d in candidates])

    session_bars: list[Bar] = []
    lookback_filled: list[tuple[datetime, list[Bar]]] = []
    for d, bars in zip(candidates, per_day):
        if d == base:
            session_bars = bars
        elif bars:
            lookback_filled.append((d, bars))

    # Take the most recent lookback_days non-empty days (ascending order).
    selected = lookback_filled[-lookback_days:] if lookback_days > 0 else []

    merged: list[Bar] = []
    data_window_start: datetime | None = None
    for d, bars in selected:
        if data_window_start is None:
            data_window_start = d
        merged.extend(bars)
    merged.extend(session_bars)
    return merged, data_window_start


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

    `lookback_days` extends the fetch window backwards from session_date
    so a strategy that needs N-bar warmup can satisfy it before the
    session_date's own bars arrive.

    After `run()` completes, `data_window_start` holds the earliest date
    that contributed lookback bars (None when lookback_days=0 or all
    lookback candidates were empty/holiday).
    """

    source: BarSource = None  # type: ignore[assignment]
    symbol: str = ""
    session_date: datetime | None = None
    interval: str = "1m"
    pace_seconds: float | str | None = None
    lookback_days: int = 0
    name: str = "source_backed_bus"
    data_window_start: datetime | None = field(default=None, init=False)

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if self.source is None or not self.symbol or self.session_date is None:
            raise ValueError(
                "SourceBackedBus requires source + symbol + session_date"
            )
        bars, self.data_window_start = await _fetch_window(
            self.source, self.symbol, self.session_date, self.interval,
            self.lookback_days,
        )
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

    After `run()` completes, `data_window_start` holds the earliest
    non-empty lookback date across all symbols.
    """

    source: BarSource = None  # type: ignore[assignment]
    symbols: list[str] = field(default_factory=list)
    session_date: datetime | None = None
    interval: str = "1m"
    pace_seconds: float | str | None = None
    lookback_days: int = 0
    name: str = "multi_symbol_source_backed_bus"
    data_window_start: datetime | None = field(default=None, init=False)

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        if self.source is None or not self.symbols or self.session_date is None:
            raise ValueError(
                "MultiSymbolSourceBackedBus requires source + symbols + session_date"
            )
        results = await asyncio.gather(*[
            _fetch_window(
                self.source, sym, self.session_date, self.interval,
                self.lookback_days,
            )
            for sym in self.symbols
        ])
        per_symbol_bars = [bars for bars, _ in results]
        window_starts = [ws for _, ws in results if ws is not None]
        # Earliest non-empty lookback date across all symbols.
        self.data_window_start = min(window_starts) if window_starts else None

        # heapq.merge sorts by Bar.timestamp; each per-symbol list is
        # already in timestamp order from the source contract.
        merged = list(heapq.merge(*per_symbol_bars, key=lambda b: b.timestamp))
        replay = ReplayBarBus(
            bars=merged, pace_seconds=self.pace_seconds, name=self.name,
        )
        await replay.run(out_queue, shutdown_queue)


__all__ = ["BarSource", "SourceBackedBus", "MultiSymbolSourceBackedBus"]
