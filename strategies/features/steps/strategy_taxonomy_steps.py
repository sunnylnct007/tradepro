"""Steps for strategy_taxonomy.feature — pins the STRATEGY_TAXONOMY
contract per IMPROVEMENT_SUGGESTIONS_v1.md §1."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.factor_types import (
    horizon_for,
    strategy_type_for,
)


@given('the strategy named "{name}"')
def step_strategy_name(context, name: str) -> None:
    context.strategy_name = name


@when('I look up its taxonomy')
def step_lookup(context) -> None:
    context.horizon = horizon_for(context.strategy_name)
    context.strategy_type = strategy_type_for(context.strategy_name)


@then('the horizon is "{expected}"')
def step_check_horizon(context, expected: str) -> None:
    assert context.horizon == expected, (
        f"horizon: expected {expected!r}, got {context.horizon!r}"
    )


@then('the strategy_type is "{expected}"')
def step_check_strategy_type(context, expected: str) -> None:
    assert context.strategy_type == expected, (
        f"strategy_type: expected {expected!r}, got {context.strategy_type!r}"
    )


@then('the horizon is None')
def step_check_horizon_none(context) -> None:
    assert context.horizon is None, (
        f"horizon: expected None, got {context.horizon!r}"
    )


@then('the strategy_type is None')
def step_check_strategy_type_none(context) -> None:
    assert context.strategy_type is None, (
        f"strategy_type: expected None, got {context.strategy_type!r}"
    )
