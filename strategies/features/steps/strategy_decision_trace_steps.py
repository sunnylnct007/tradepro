"""Steps for strategy_decision_trace.feature — covers the per-bar
decision log surface exposed on Strategy and re-exported into the
ledger snapshot by the engine."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from behave import given, then, when

from tradepro_strategies.paper.engine import Engine, StrategyRegistration
from tradepro_strategies.paper.ledger import Ledger
from tradepro_strategies.paper.strategies.ichimoku_fx_mr import (
    IchimokuFXMeanReversionStrategy,
)
from tradepro_strategies.paper.strategy import Bar


def _bar(symbol: str, t: datetime, close: float) -> Bar:
    # 1h FX bars; OHLC are deterministic walks so the warmup gate is
    # the only thing that fires for the first N bars.
    return Bar(
        symbol=symbol,
        timestamp=t,
        open=close - 0.0005,
        high=close + 0.0010,
        low=close - 0.0010,
        close=close,
        volume=0,
        timeframe_seconds=3600,
    )


def _drive(strategy: IchimokuFXMeanReversionStrategy, symbol: str, n_bars: int) -> None:
    t0 = datetime(2026, 5, 25, 8, 0, tzinfo=timezone.utc)
    price = 1.0800
    for i in range(n_bars):
        # Gentle drift so post-warmup bars produce a non-zero signal.
        price += 0.0015 * ((-1) ** i)
        strategy.on_bar(_bar(symbol, t0 + timedelta(hours=i), price))


@given("a fresh ichimoku_fx_mr strategy with warmup_bars = {n:d}")
def step_fresh_strategy(context, n: int) -> None:
    context.strategy = IchimokuFXMeanReversionStrategy(
        strategy_id="ichimoku_fx_mr",
        params={"warmup_bars": n, "pairs": ["EURUSD"]},
    )


@when("I feed it {n:d} EURUSD bars before it warms up")
def step_feed_pre_warmup(context, n: int) -> None:
    _drive(context.strategy, "EURUSD", n)


@when("I feed it {n:d} EURUSD bars")
def step_feed_bars(context, n: int) -> None:
    _drive(context.strategy, "EURUSD", n)


@then('its decision trace contains {n:d} "skip-warmup" entries for EURUSD')
def step_skip_warmup_count(context, n: int) -> None:
    decisions = context.strategy.recent_decisions()
    warm = [
        d for d in decisions
        if d["symbol"] == "EURUSD" and d["action"] == "skip-warmup"
    ]
    assert len(warm) == n, (
        f"expected {n} skip-warmup entries, got {len(warm)}: {warm}"
    )


@then("each skip-warmup entry carries bars_seen and bars_required in its detail")
def step_skip_warmup_detail(context) -> None:
    for d in context.strategy.recent_decisions():
        if d["action"] != "skip-warmup":
            continue
        detail = d.get("detail", {})
        assert "bars_seen" in detail and "bars_required" in detail, (
            f"missing detail fields on {d}"
        )


@then('its decision trace contains at least one non-warmup entry for EURUSD')
def step_has_non_warmup(context) -> None:
    decisions = context.strategy.recent_decisions()
    others = [
        d for d in decisions
        if d["symbol"] == "EURUSD" and d["action"] != "skip-warmup"
    ]
    assert others, (
        f"expected at least one post-warmup decision, got only: "
        f"{[d['action'] for d in decisions]}"
    )


@given(
    "an engine wired with an ichimoku_fx_mr strategy that has logged a "
    "skip-warmup decision"
)
def step_engine_with_strategy(context) -> None:
    # Use the real ichimoku strategy so we exercise the same buffer
    # plumbing the production CLI does. We bypass Engine.run because
    # the bar bus + asyncio chain isn't what's under test here — we
    # only need attach_decisions to walk the registry.
    strategy = IchimokuFXMeanReversionStrategy(
        strategy_id="ichimoku_fx_mr",
        params={"warmup_bars": 5, "pairs": ["EURUSD"]},
    )
    _drive(strategy, "EURUSD", 2)
    engine = Engine(bus=None, router=None, ledger=Ledger())  # type: ignore[arg-type]
    engine.registrations[strategy.strategy_id] = StrategyRegistration(
        strategy=strategy, symbols={"EURUSD"}, capital_usd=10_000.0,
    )
    engine.ledger.register(strategy.strategy_id)
    context.engine = engine
    context.strategy = strategy


@when("I take a ledger snapshot via engine.attach_decisions")
def step_take_snapshot(context) -> None:
    snapshot = context.engine.ledger.to_snapshot()
    context.engine.attach_decisions(snapshot)
    context.snapshot = snapshot


@then('the snapshot\'s strategy entry has a populated "decisions" list')
def step_snapshot_has_decisions(context) -> None:
    entries = context.snapshot.get("strategies", [])
    assert entries, "snapshot has no strategy entries"
    entry = next(e for e in entries if e["strategy_id"] == "ichimoku_fx_mr")
    decisions = entry.get("decisions")
    assert isinstance(decisions, list) and decisions, (
        f"expected non-empty decisions list, got {decisions!r}"
    )


@then("the snapshot round-trips through json.dumps without error")
def step_snapshot_serialisable(context) -> None:
    # default=str catches any datetime stragglers — the production
    # CLI uses the same fallback. A plain dumps must work because the
    # decision records themselves only contain primitives.
    json.dumps(context.snapshot)
