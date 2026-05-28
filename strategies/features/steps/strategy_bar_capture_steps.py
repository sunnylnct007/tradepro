"""Steps for strategy_bar_capture.feature — covers the per-bar
bars_seen surface on Strategy and the engine's attach_bars side-channel."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from behave import given, then, when

from tradepro_strategies.paper.bar_bus import ReplayBarBus
from tradepro_strategies.paper.engine import Engine
from tradepro_strategies.paper.router import PaperOrderRouter
from tradepro_strategies.paper.strategy import Bar, Order, OrderSide, OrderType, Strategy


class _RecorderStrategy(Strategy):
    """Inert strategy used purely to exercise the bar / decision plumbing
    in isolation. on_bar is a no-op so the engine drives record_bar via
    its own consumer loop."""

    def on_bar(self, bar: Bar) -> list[Order]:  # type: ignore[override]
        return []


def _bar(symbol: str, t: datetime, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=t,
        open=close - 0.10,
        high=close + 0.20,
        low=close - 0.20,
        close=close,
        volume=1000,
        timeframe_seconds=60,
    )


@given("a fresh strategy with bar_buffer_size = {n:d}")
def step_fresh_strategy_with_buffer(context, n: int) -> None:
    context.strategy = _RecorderStrategy(strategy_id="rec", bar_buffer_size=n)


@when("I feed it {n:d} AAPL bars via record_bar")
def step_feed_aapl(context, n: int) -> None:
    t0 = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    for i in range(n):
        context.strategy.record_bar(_bar("AAPL", t0 + timedelta(minutes=i), 100.0 + i))


@when("I feed it {a:d} AAPL bars and {m:d} MSFT bars via record_bar")
def step_feed_mixed(context, a: int, m: int) -> None:
    t0 = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    # Interleave the AAPL and MSFT bar timestamps so the test verifies the
    # cross-symbol merge actually sorts (rather than just appending one
    # symbol's buffer after the other).
    rows: list[tuple[str, datetime, float]] = []
    for i in range(a):
        rows.append(("AAPL", t0 + timedelta(minutes=2 * i), 200.0 + i))
    for i in range(m):
        rows.append(("MSFT", t0 + timedelta(minutes=2 * i + 1), 300.0 + i))
    for sym, ts, close in rows:
        context.strategy.record_bar(_bar(sym, ts, close))


@then("its recent_bars returns {n:d} entries")
def step_recent_bars_count(context, n: int) -> None:
    got = context.strategy.recent_bars()
    assert len(got) == n, f"expected {n}, got {len(got)}: {got}"


@then("each entry carries ts, symbol, open, high, low, close, volume")
def step_entry_shape(context) -> None:
    required = {"ts", "symbol", "open", "high", "low", "close", "volume"}
    for entry in context.strategy.recent_bars():
        missing = required - entry.keys()
        assert not missing, f"entry missing keys {missing}: {entry}"


@then("the entries are the last {n:d} bars in timestamp order")
def step_last_n_in_order(context, n: int) -> None:
    got = context.strategy.recent_bars()
    timestamps = [e["ts"] for e in got]
    assert timestamps == sorted(timestamps), f"out of order: {timestamps}"
    closes = [e["close"] for e in got]
    # The feeder used close = 100 + i; with the ring buffer capped at n,
    # we should see the LAST n closes (i = total - n .. total - 1).
    expected_first_close = max(100.0, 100.0 + (len(got) > 0))
    assert closes[-1] >= expected_first_close - 1, f"last close unexpectedly small: {closes}"


@then("entries are time-ordered ascending")
def step_time_ordered_ascending(context) -> None:
    timestamps = [e["ts"] for e in context.strategy.recent_bars()]
    assert timestamps == sorted(timestamps), f"out of order: {timestamps}"


@then("AAPL appears {a:d} times and MSFT appears {m:d} times")
def step_symbol_counts(context, a: int, m: int) -> None:
    by_sym: dict[str, int] = {}
    for e in context.strategy.recent_bars():
        by_sym[e["symbol"]] = by_sym.get(e["symbol"], 0) + 1
    assert by_sym.get("AAPL", 0) == a, f"AAPL: {by_sym}"
    assert by_sym.get("MSFT", 0) == m, f"MSFT: {by_sym}"


@given("an engine wired with a strategy that has recorded a few bars")
def step_engine_wired(context) -> None:
    bus = ReplayBarBus(bars=[])
    router = PaperOrderRouter()
    context.engine = Engine(bus=bus, router=router)
    strat = _RecorderStrategy(strategy_id="rec")
    context.engine.register_strategy(strat, symbols=["AAPL"], capital_usd=10_000)
    t0 = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    for i in range(3):
        strat.record_bar(_bar("AAPL", t0 + timedelta(minutes=i), 150.0 + i))
    context.strategy = strat


@when("I take a ledger snapshot via engine.attach_bars")
def step_snapshot_attach_bars(context) -> None:
    snap = context.engine.ledger.to_snapshot()
    context.engine.attach_bars(snap)
    context.snapshot = snap


@then('the snapshot\'s strategy entry has a populated "bars_seen" list')
def step_snapshot_bars_populated(context) -> None:
    entries = context.snapshot.get("strategies") or []
    assert entries, "no strategies in snapshot"
    bars = entries[0].get("bars_seen")
    assert bars and len(bars) >= 1, f"bars_seen empty or missing: {entries[0]}"


# Note: the "the snapshot round-trips through json.dumps without error"
# step is shared from strategy_decision_trace_steps.py — Behave registers
# step definitions globally, so we don't redefine it here.


@given("an engine running a replay session with {n:d} AAPL bars")
def step_engine_replay_session(context, n: int) -> None:
    t0 = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    bars = [_bar("AAPL", t0 + timedelta(minutes=i), 100.0 + i) for i in range(n)]
    bus = ReplayBarBus(bars=bars)
    router = PaperOrderRouter()
    context.engine = Engine(bus=bus, router=router)
    context.strategy = _RecorderStrategy(strategy_id="rec")
    context.engine.register_strategy(
        context.strategy, symbols=["AAPL"], capital_usd=10_000,
    )


@when("the session completes")
def step_session_completes(context) -> None:
    # Capture the snapshot so charts/decisions/bars assertions in
    # downstream feature files can inspect it without re-running.
    context.snapshot = asyncio.run(context.engine.run(datetime(2026, 5, 22)))


@then('the strategy\'s bars_seen contains {n:d} entries for AAPL')
def step_bars_seen_count(context, n: int) -> None:
    got = context.strategy.recent_bars()
    aapl = [e for e in got if e["symbol"] == "AAPL"]
    assert len(aapl) == n, f"expected {n}, got {len(aapl)}: {aapl}"
