"""Steps for adjusted_close.feature — pins the split-aware contract."""
from __future__ import annotations

import pandas as pd
from behave import given, then, when


@given("a flat-adjusted price series with a 4:1 split mid-window")
def step_split_series(context):
    # 300 trading days; raw close is flat at 100 for the first 150
    # then jumps to 25 (4:1 split made each share quarter of its old
    # price). adj_close stays smooth at 100 throughout — that's how
    # Yahoo presents post-split data: history is back-adjusted.
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    close = [100.0] * 150 + [25.0] * 150
    adj = [25.0] * 300  # adjusted price is the post-split price held flat
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close,
         "close": close, "adj_close": adj, "volume": [1_000_000] * 300},
        index=idx,
    )
    context.prices = df


@given("a flat raw-only price series")
def step_flat_raw(context):
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    close = [50.0] * 300
    df = pd.DataFrame(
        {"open": close, "high": close, "low": close,
         "close": close, "volume": [500_000] * 300},
        index=idx,
    )
    context.prices = df


@when("I compute the market_state for it")
def step_compute_state(context):
    from tradepro_strategies.market_state import market_state
    context.state = market_state("TEST", context.prices)


@then("the percentage off the 52w high is approximately 0%")
def step_pct_off_high(context):
    pct = context.state.pct_off_52w_high_pct
    assert pct is not None, "pct_off_52w_high_pct is None"
    assert abs(pct) < 1.0, f"expected ~0%, got {pct:.2f}%"


@then("the drawdown from peak is approximately 0%")
def step_drawdown_zero(context):
    dd = context.state.drawdown_from_peak_pct
    assert dd is not None, "drawdown_from_peak_pct is None"
    assert abs(dd) < 1.0, f"expected ~0%, got {dd:.2f}%"
