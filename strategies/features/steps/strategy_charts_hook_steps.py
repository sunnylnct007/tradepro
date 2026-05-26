"""Steps for strategy_charts_hook.feature — pin Strategy.recent_charts
+ Engine.attach_charts behaviour."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from behave import given, then, when

from tradepro_strategies.paper.bar_bus import ReplayBarBus
from tradepro_strategies.paper.engine import Engine
from tradepro_strategies.paper.router import PaperOrderRouter
from tradepro_strategies.paper.strategy import Bar, Order, Strategy


_CHART_NAME = "demo_chart"


def _bar(symbol: str, t: datetime, close: float) -> Bar:
    return Bar(
        symbol=symbol, timestamp=t,
        open=close - 0.10, high=close + 0.20,
        low=close - 0.20, close=close,
        volume=1000, timeframe_seconds=60,
    )


class _FakeChartStrategy(Strategy):
    """Returns a single tiny figure dict — enough to assert attach
    plumbing without depending on plotly being installed."""

    def on_bar(self, bar: Bar) -> list[Order]:  # type: ignore[override]
        return []

    def recent_charts(self) -> dict[str, dict]:  # type: ignore[override]
        return {_CHART_NAME: {"data": [], "layout": {}}}


class _BuggyChartStrategy(Strategy):
    """Raises inside recent_charts — Engine.attach_charts must
    swallow + attach an empty dict so the snapshot survives."""

    def on_bar(self, bar: Bar) -> list[Order]:  # type: ignore[override]
        return []

    def recent_charts(self) -> dict[str, dict]:  # type: ignore[override]
        raise RuntimeError("deliberately broken chart builder")


@given("a base Strategy instance")
def step_base_strategy(context) -> None:
    class _Plain(Strategy):
        def on_bar(self, bar: Bar) -> list[Order]:  # type: ignore[override]
            return []
    context.strategy = _Plain(strategy_id="plain")


@when("I call recent_charts()")
def step_call_recent_charts(context) -> None:
    context.charts = context.strategy.recent_charts()


@then("the result is an empty dict")
def step_empty_dict(context) -> None:
    assert isinstance(context.charts, dict), type(context.charts)
    assert context.charts == {}, context.charts


def _build_engine(strategy: Strategy) -> Engine:
    t0 = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    bars = [_bar("AAPL", t0 + timedelta(minutes=i), 100.0 + i) for i in range(3)]
    bus = ReplayBarBus(bars=bars)
    router = PaperOrderRouter()
    engine = Engine(bus=bus, router=router)
    engine.register_strategy(strategy, symbols=["AAPL"], capital_usd=10_000)
    return engine


@given("an engine running a replay session with a fake-chart strategy")
def step_engine_with_fake_chart_strategy(context) -> None:
    context.strategy = _FakeChartStrategy(strategy_id="fake")
    context.engine = _build_engine(context.strategy)


@given("an engine running a replay session with a buggy-chart strategy")
def step_engine_with_buggy_chart_strategy(context) -> None:
    context.strategy = _BuggyChartStrategy(strategy_id="buggy")
    context.engine = _build_engine(context.strategy)




# Note: "the session completes" is already defined by
# strategy_bar_capture_steps.py and now stores the snapshot on
# context.snapshot. Behave registers globally so we reuse it
# directly.


@then('the snapshot\'s strategy entry has a "charts" key')
def step_snapshot_has_charts_key(context) -> None:
    entries = context.snapshot.get("strategies") or []
    assert entries, "no strategies in snapshot"
    assert "charts" in entries[0], entries[0]


@then("the charts entry contains the figure name the strategy emitted")
def step_charts_contains_figure(context) -> None:
    charts = context.snapshot["strategies"][0]["charts"]
    assert _CHART_NAME in charts, f"expected {_CHART_NAME!r} in {charts}"


@then("the charts dict is empty")
def step_charts_empty(context) -> None:
    charts = context.snapshot["strategies"][0]["charts"]
    assert charts == {}, charts
