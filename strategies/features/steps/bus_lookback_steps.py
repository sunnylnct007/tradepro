"""Steps for bus_lookback.feature — exercises the lookback_days knob on
SourceBackedBus / MultiSymbolSourceBackedBus with a stub BarSource so
the test is offline."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

from behave import given, then, when

from tradepro_strategies.paper.messages import BarEvent
from tradepro_strategies.paper.sources.base import (
    BarSource,
    MultiSymbolSourceBackedBus,
    SourceBackedBus,
)
from tradepro_strategies.paper.strategy import Bar


@dataclass
class _DayStubSource(BarSource):
    """Returns a fixed number of bars per (symbol, day) and records each call.

    `empty_dates` marks specific date strings (YYYY-MM-DD) that should
    return empty — used to simulate bank holidays for the holiday-aware
    lookback tests.
    """

    name: str = "day_stub"
    calls: list[tuple[str, str]] = field(default_factory=list)
    bars_per_day: int = 4
    empty_dates: set[str] = field(default_factory=set)

    async def fetch(self, symbol: str, session_date: datetime, interval: str) -> list[Bar]:
        date_str = session_date.date().isoformat()
        self.calls.append((symbol, date_str))
        if date_str in self.empty_dates:
            return []
        base = session_date.replace(hour=14, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
        return [
            Bar(
                symbol=symbol,
                timestamp=base + timedelta(hours=i),
                open=1.0, high=1.0, low=1.0, close=1.0,
                volume=0, timeframe_seconds=3600,
            )
            for i in range(self.bars_per_day)
        ]


def _drain(bus) -> list[BarEvent]:
    async def go():
        out_q: asyncio.Queue = asyncio.Queue()
        sd_q: asyncio.Queue = asyncio.Queue()
        await bus.run(out_q, sd_q)
        events = []
        while not out_q.empty():
            events.append(out_q.get_nowait())
        return events
    events = asyncio.run(go())
    return [e for e in events if isinstance(e, BarEvent)]


@given("a stub bar source serving 4 days of EURUSD bars per call")
def step_stub_single(context) -> None:
    context.source = _DayStubSource()


@given("a stub bar source serving 2 days of bars per call for EURUSD and GBPUSD")
def step_stub_multi(context) -> None:
    context.source = _DayStubSource()


@given("a stub bar source where {date} returns empty (bank holiday)")
def step_stub_with_holiday(context, date: str) -> None:
    context.source = _DayStubSource(empty_dates={date})


@when(
    "I run SourceBackedBus with session_date=2026-05-22 and lookback_days={n:d}"
)
def step_run_single_bus(context, n: int) -> None:
    bus = SourceBackedBus(
        source=context.source,
        symbol="EURUSD",
        session_date=datetime(2026, 5, 22),
        interval="1h",
        lookback_days=n,
    )
    context.events = _drain(bus)
    context.bus = bus


@when(
    "I run MultiSymbolSourceBackedBus with session_date=2026-05-22 and lookback_days={n:d}"
)
def step_run_multi_bus(context, n: int) -> None:
    bus = MultiSymbolSourceBackedBus(
        source=context.source,
        symbols=["EURUSD", "GBPUSD"],
        session_date=datetime(2026, 5, 22),
        interval="1h",
        lookback_days=n,
    )
    context.events = _drain(bus)
    context.bus = bus


@when(
    "I run SourceBackedBus with session_date=2026-05-27 and lookback_days={n:d}"
)
def step_run_single_bus_holiday(context, n: int) -> None:
    # 2026-05-25 (Monday) = UK May bank holiday; 2026-05-27 = Wednesday
    bus = SourceBackedBus(
        source=context.source,
        symbol="EURUSD",
        session_date=datetime(2026, 5, 27),
        interval="1d",
        lookback_days=n,
    )
    context.events = _drain(bus)
    context.bus = bus


@then("the source is called once per day in the lookback window")
def step_calls_per_day(context) -> None:
    # Holiday-aware lookback generates extra candidate days for the buffer.
    # Assert that the required lookback + session dates were called.
    dates = sorted({d for _, d in context.source.calls})
    required = ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22"]
    assert all(r in dates for r in required), (
        f"required dates {required} not all present in {dates}"
    )


@then("the bus emits 4 days worth of bars in timestamp order")
def step_emit_window(context) -> None:
    assert len(context.events) == 16, f"expected 16 bars (4 days * 4 per day), got {len(context.events)}"
    timestamps = [e.bar.timestamp for e in context.events]
    assert timestamps == sorted(timestamps), f"out of order: {timestamps}"


@then("the source is called exactly once")
def step_source_called_once(context) -> None:
    assert len(context.source.calls) == 1, f"expected 1 call, got {context.source.calls}"


@then("the source is called twice per symbol")
def step_two_calls_per_symbol(context) -> None:
    by_sym: dict[str, int] = {}
    for sym, _ in context.source.calls:
        by_sym[sym] = by_sym.get(sym, 0) + 1
    # Holiday-aware lookback fetches extra candidate days; assert at least 2 per symbol.
    assert by_sym.get("EURUSD", 0) >= 2, f"EURUSD calls: {by_sym}"
    assert by_sym.get("GBPUSD", 0) >= 2, f"GBPUSD calls: {by_sym}"


@then("the emitted stream contains both EURUSD and GBPUSD bars in timestamp order")
def step_multi_stream(context) -> None:
    symbols = {e.bar.symbol for e in context.events}
    assert symbols == {"EURUSD", "GBPUSD"}, f"symbols: {symbols}"
    timestamps = [e.bar.timestamp for e in context.events]
    assert timestamps == sorted(timestamps), f"out of order: {timestamps}"


@then("the bus still emits {n:d} days worth of bars in timestamp order")
def step_emit_n_days(context, n: int) -> None:
    expected_bars = n * context.source.bars_per_day
    assert len(context.events) == expected_bars, (
        f"expected {expected_bars} bars ({n} days × {context.source.bars_per_day}), "
        f"got {len(context.events)}"
    )
    timestamps = [e.bar.timestamp for e in context.events]
    assert timestamps == sorted(timestamps), f"out of order: {timestamps}"


@then("data_window_start is set on the bus")
def step_data_window_start_set(context) -> None:
    bus = context.bus
    assert bus.data_window_start is not None, (
        f"expected data_window_start to be set, got None"
    )


@then("data_window_start points to a trading day before the holiday")
def step_data_window_before_holiday(context) -> None:
    bus = context.bus
    assert bus.data_window_start is not None, "data_window_start is None"
    # 2026-05-25 (Monday) is the bank holiday. session_date=2026-05-27 (Wed),
    # lookback_days=1 → most recent prior non-empty day is 2026-05-26 (Tue).
    ws = bus.data_window_start.date().isoformat()
    assert ws != "2026-05-25", f"data_window_start landed on the holiday: {ws}"
    assert ws <= "2026-05-26", f"data_window_start {ws} is not before session_date"
