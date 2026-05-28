"""Steps for strategy_seed_positions.feature — pins the
Strategy.seed_positions contract on both the base no-op and the
ichimoku_fx_mr override."""
from __future__ import annotations

import json

from behave import then, when

from tradepro_strategies.paper.strategies.ichimoku_fx_mr import (
    IchimokuFXMeanReversionStrategy,
)


# Note: the Given step "a fresh ichimoku_fx_mr strategy with warmup_bars = N"
# is reused from strategy_decision_trace_steps.py — Behave merges step
# registries across modules. Same for "a fresh strategy with
# bar_buffer_size = N" from strategy_bar_capture_steps.py.


@when('I seed positions {payload}')
def step_seed(context, payload: str) -> None:
    positions = json.loads(payload)
    context.strategy.seed_positions(positions)


# Note: "no exception is raised" is defined in compass_scorer_steps.py;
# Behave registers globally so we reuse it here.


@then("the strategy's recent_bars stays empty")
def step_no_bars(context) -> None:
    assert context.strategy.recent_bars() == [], (
        f"expected no bars, got {context.strategy.recent_bars()}"
    )


@then('the strategy reports current position {pair} = {qty:d}')
def step_position_is(context, pair: str, qty: int) -> None:
    assert isinstance(context.strategy, IchimokuFXMeanReversionStrategy), (
        "this step targets the FX strategy specifically"
    )
    got = context.strategy._fx_positions.get(pair)
    assert got == qty, f"expected {pair}={qty}, got {got}"
