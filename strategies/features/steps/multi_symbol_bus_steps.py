"""Steps for multi_symbol_bus.feature — exercises the paper bus in
isolation with a stub BarSource (no network)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from behave import given, then, when

from tradepro_strategies.paper.messages import BarEvent, ShutdownEvent
from tradepro_strategies.paper.sources.base import (
    BarSource,
    MultiSymbolSourceBackedBus,
)
from tradepro_strategies.paper.strategy import Bar


@dataclass
class _StubSource(BarSource):
    """Returns deterministic, pre-staggered bars per symbol so the
    merge step has something non-trivial to interleave."""

    name: str = "stub"
    offsets_by_symbol: dict[str, list[int]] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)

    async def fetch(self, symbol: str, session_date: datetime, interval: str) -> list[Bar]:
        self.fetched.append(symbol)
        t0 = datetime(2026, 5, 25, 14, 30, tzinfo=timezone.utc)
        offsets = self.offsets_by_symbol.get(symbol, [])
        return [
            Bar(
                symbol=symbol,
                timestamp=t0 + timedelta(minutes=o),
                open=100.0, high=101.0, low=99.0, close=100.5,
                volume=1000, timeframe_seconds=60,
            )
            for o in offsets
        ]


def _run_bus(context) -> None:
    """Drain the bus into context.events for the Then steps to inspect."""
    bus = MultiSymbolSourceBackedBus(
        source=context.source,
        symbols=context.symbols,
        session_date=datetime(2026, 5, 25),
        interval="1m",
    )

    async def _drive():
        out_q: asyncio.Queue = asyncio.Queue()
        sd_q: asyncio.Queue = asyncio.Queue()
        await bus.run(out_q, sd_q)
        events = []
        while not out_q.empty():
            events.append(out_q.get_nowait())
        return events

    context.events = asyncio.run(_drive())


@given("a stub bar source serving 3 symbols with staggered minute bars")
def step_stub_source_three_symbols(context) -> None:
    # AAPL even minutes, MSFT odd, NVDA every minute — guarantees
    # the merge has to interleave to produce sorted output.
    context.symbols = ["AAPL", "MSFT", "NVDA"]
    context.source = _StubSource(offsets_by_symbol={
        "AAPL": [0, 2, 4],
        "MSFT": [1, 3, 5],
        "NVDA": [0, 1, 2, 3, 4, 5],
    })


@given("a stub bar source where one of 3 symbols returns no bars")
def step_stub_source_one_empty(context) -> None:
    context.symbols = ["AAPL", "MSFT", "NVDA"]
    context.source = _StubSource(offsets_by_symbol={
        "AAPL": [0, 1, 2],
        "MSFT": [],          # source returns [] for this symbol
        "NVDA": [0, 1, 2],
    })


@when("I run the multi-symbol bus for those symbols")
def step_run_bus(context) -> None:
    _run_bus(context)


@then("the source fetch is invoked once per symbol")
def step_fetch_per_symbol(context) -> None:
    assert sorted(context.source.fetched) == sorted(context.symbols), (
        f"expected {sorted(context.symbols)}, got {sorted(context.source.fetched)}"
    )


@then("every emitted bar's timestamp is greater than or equal to the previous one")
def step_timestamps_monotonic(context) -> None:
    bar_events = [e for e in context.events if isinstance(e, BarEvent)]
    timestamps = [b.bar.timestamp for b in bar_events]
    assert timestamps == sorted(timestamps), (
        f"timestamps not monotonic: {timestamps}"
    )


@then("every requested symbol appears in the emitted stream")
def step_all_symbols_emitted(context) -> None:
    bar_events = [e for e in context.events if isinstance(e, BarEvent)]
    seen = {b.bar.symbol for b in bar_events}
    assert seen == set(context.symbols), (
        f"expected {set(context.symbols)}, got {seen}"
    )


@then("the final queued event is a ShutdownEvent")
def step_terminal_shutdown(context) -> None:
    assert context.events, "no events emitted at all"
    assert isinstance(context.events[-1], ShutdownEvent), (
        f"last event is {type(context.events[-1]).__name__}, not ShutdownEvent"
    )


@then("the bus completes without error")
def step_completes_without_error(context) -> None:
    # _run_bus already ran asyncio.run(...) — reaching this step means
    # no exception was raised. Spot-check that we got at least one event.
    assert context.events, "bus produced no events"


@then("only the symbols with bars appear in the emitted stream")
def step_only_nonempty_symbols(context) -> None:
    bar_events = [e for e in context.events if isinstance(e, BarEvent)]
    seen = {b.bar.symbol for b in bar_events}
    expected = {s for s, offsets in context.source.offsets_by_symbol.items() if offsets}
    assert seen == expected, f"expected {expected}, got {seen}"
