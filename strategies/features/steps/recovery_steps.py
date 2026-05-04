"""Steps for recovery.feature — pure-function tests of the
backtest's drawdown-recovery stat."""
from __future__ import annotations

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.backtest import _compute_stats


@given("a synthetic equity curve that drew down {dd:d}% and reclaimed the prior peak after {days:d} days")
def step_recovered_curve(context, dd: int, days: int):
    # Construct: rise to 12000, fall to (12000 * (100-dd)/100), rise back to 12000.
    # The "after N days" target is the calendar-day distance between the
    # trough and recovery; we use business days plus weekends so the
    # bdate_range elapsed-day count matches the asserted calendar count.
    idx = pd.bdate_range("2024-01-01", periods=400)
    trough_value = 12000 * (100 - dd) / 100
    eq = pd.Series(
        np.concatenate([
            np.linspace(10000, 12000, 100),
            np.linspace(12000, trough_value, 100),
            np.linspace(trough_value, 12000, 100),
            np.linspace(12000, 14000, 100),
        ]),
        index=idx,
    )
    context.equity = eq


@given("a synthetic equity curve that drew down {dd:d}% and never reclaimed the prior peak")
def step_unrecovered_curve(context, dd: int):
    idx = pd.bdate_range("2024-01-01", periods=400)
    trough_value = 12000 * (100 - dd) / 100
    eq = pd.Series(
        np.concatenate([
            np.linspace(10000, 12000, 100),
            np.linspace(12000, trough_value, 300),  # decline to end
        ]),
        index=idx,
    )
    context.equity = eq


@when("I compute the backtest stats")
def step_compute(context):
    context.stats = _compute_stats(context.equity, 10000)


@then("the max-DD is approximately {expected:d}%")
def step_assert_dd(context, expected: int):
    actual = context.stats["max_drawdown_pct"]
    assert abs(actual - expected) < 1.0, f"expected ~{expected}%, got {actual:.2f}%"


@then("max_drawdown_recovery_days is approximately {expected:d}")
def step_assert_days(context, expected: int):
    actual = context.stats["max_drawdown_recovery_days"]
    assert actual is not None, "recovery_days is None"
    # Allow a generous fudge — bdate_range vs calendar days have weekends.
    assert abs(actual - expected) < 30, f"expected ~{expected}, got {actual}"


@then("max_drawdown_recovery_days is null")
def step_assert_days_null(context):
    actual = context.stats["max_drawdown_recovery_days"]
    assert actual is None, f"expected None, got {actual!r}"


@then("max_drawdown_still_recovering is {expected}")
def step_assert_still(context, expected: str):
    actual = context.stats["max_drawdown_still_recovering"]
    expected_bool = {"True": True, "False": False}[expected]
    assert actual is expected_bool, f"expected {expected_bool}, got {actual!r}"


@then("days_since_max_dd_trough is at least {n:d}")
def step_assert_days_since(context, n: int):
    actual = context.stats["days_since_max_dd_trough"]
    assert actual is not None, "days_since_max_dd_trough is None"
    assert actual >= n, f"expected ≥{n}, got {actual}"
