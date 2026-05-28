"""Steps for market_state_fields.feature — pin the closes_30d field
the email digest + PDF rely on.

Reuses the existing `When I compute the market state` step from
range_position_steps.py (single source of truth for that action)."""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd
from behave import given, then


def _series(prices: list[float]) -> pd.DataFrame:
    # Pad date range so freq="B" rounding near holidays can't silently
    # trim a price (the existing market_state_classify steps handle this
    # by trimming from the prices side; here the prices are *the thing
    # under test*, so we trim dates instead — keeps every NaN we placed).
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=len(prices) + 10, freq="B")
    return pd.DataFrame(
        {"adj_close": prices, "close": prices},
        index=dates[-len(prices):],
    )


@given("a synthetic price series of {count:d} daily closes")
def step_series_n(context, count: int) -> None:
    # Gentle linear walk so the engine can compute its derivatives;
    # we only care about the closes_30d field downstream.
    context.prices = _series([100.0 + i * 0.1 for i in range(count)])


@given("a synthetic price series of 30 closes with 3 NaNs at the end")
def step_series_with_nans(context) -> None:
    base = [100.0 + i * 0.1 for i in range(27)] + [float("nan")] * 3
    # Pad the front so SMA / RSI windows can compute, then keep the last
    # 30 (which contain the NaNs we want to test the filter against).
    padded = [100.0] * 230 + base
    context.prices = _series(padded)


@then("closes_30d has {count:d} entries")
def step_closes_count(context, count: int) -> None:
    assert len(context.state.closes_30d) == count, (
        f"expected {count} closes_30d entries, got {len(context.state.closes_30d)}"
    )


@then("the last value of closes_30d equals the last close")
def step_last_matches(context) -> None:
    last_close = float(context.prices["adj_close"].iloc[-1])
    last_in_field = context.state.closes_30d[-1]
    assert math.isclose(last_in_field, last_close, rel_tol=1e-9), (
        f"closes_30d tail {last_in_field} != adj_close tail {last_close}"
    )


@then("closes_30d is serialised in to_dict()")
def step_serialised(context) -> None:
    d = context.state.to_dict()
    assert "closes_30d" in d, "to_dict() omitted closes_30d"
    assert d["closes_30d"] == list(context.state.closes_30d)


@then("no entry of closes_30d is NaN")
def step_no_nan(context) -> None:
    for v in context.state.closes_30d:
        assert v == v, f"NaN slipped into closes_30d: {context.state.closes_30d}"
