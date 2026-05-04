"""Steps for cross_sectional.feature — pure ranking helper."""
from __future__ import annotations

import json

from behave import given, then, when

from tradepro_strategies.cross_sectional import rank_by_momentum


@given("the basket json {payload}")
def step_basket(context, payload: str):
    # Convert JS-style null in scenario text to Python None via JSON.
    context.basket = json.loads(payload)


@given("a basket of {n:d} symbols with monotonically decreasing returns")
def step_monotonic(context, n: int):
    context.basket = {f"S{i}": float(n - i) * 5.0 for i in range(n)}


@given("a basket of one symbol")
def step_one(context):
    context.basket = {"ALONE": 12.5}


@when("I rank the basket by momentum")
def step_rank(context):
    context.ranks = rank_by_momentum(context.basket)


@then('"{symbol}" has rank {expected:d}')
def step_assert_rank(context, symbol: str, expected: int):
    actual = context.ranks[symbol]["rank"]
    assert actual == expected, f"{symbol}: expected rank {expected}, got {actual}"


@then('"{symbol}" rank_pct is {expected:f}')
def step_assert_rank_pct(context, symbol: str, expected: float):
    actual = context.ranks[symbol]["rank_pct"]
    assert abs(actual - expected) < 1e-6, f"{symbol}: expected {expected}, got {actual}"


@then('"{symbol}" zscore is positive')
def step_zscore_positive(context, symbol: str):
    z = context.ranks[symbol]["zscore"]
    assert z is not None and z > 0, f"{symbol} zscore: {z}"


@then('"{symbol}" zscore is negative')
def step_zscore_negative(context, symbol: str):
    z = context.ranks[symbol]["zscore"]
    assert z is not None and z < 0, f"{symbol} zscore: {z}"


@then('"{symbol}" has rank None')
def step_rank_none(context, symbol: str):
    assert context.ranks[symbol]["rank"] is None


@then('"{symbol}" zscore is None')
def step_zscore_none(context, symbol: str):
    assert context.ranks[symbol]["zscore"] is None


@then('"{symbol}" peer_count is {expected:d}')
def step_peer_count(context, symbol: str, expected: int):
    actual = context.ranks[symbol]["peer_count"]
    assert actual == expected, f"{symbol}: expected peer_count {expected}, got {actual}"


@then("exactly {n:d} symbols are flagged top quartile")
def step_top_quartile_count(context, n: int):
    top = [s for s, r in context.ranks.items() if r.get("is_top_quartile")]
    assert len(top) == n, f"expected {n} top-quartile, got {len(top)}: {top}"


@then("the single symbol has zscore 0.0")
def step_single_zero(context):
    only = next(iter(context.ranks.values()))
    assert only["zscore"] == 0.0, only
