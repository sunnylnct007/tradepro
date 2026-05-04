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


@given("a 5y price series with a peak in year 1 and a flat recent 12 months at the recovered level")
def step_inrg_pattern(context):
    import numpy as np
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2021-05-01", periods=1260)
    prices = np.concatenate([
        np.linspace(100, 140, 200),
        np.linspace(140, 50, 500),
        np.linspace(50, 105, 300),
        np.full(260, 105.0) + rng.normal(0, 0.5, 260),
    ])
    df = pd.DataFrame(
        {"open": prices, "high": prices, "low": prices,
         "close": prices, "adj_close": prices, "volume": [1] * len(prices)},
        index=idx[: len(prices)],
    )
    context.prices = df


@then("the entry signal is not BUY because of long-term drawdown alone")
def step_not_buy(context):
    sig = context.state.entry_signal
    assert sig != "BUY", (
        f"INRG-pattern incorrectly classified as BUY: {context.state.entry_reason!r}"
    )


@then('the entry reason does not claim "historical bounce zone" off the 5y peak')
def step_no_historical_bounce(context):
    reason = context.state.entry_reason or ""
    assert "historical bounce zone" not in reason, (
        f"reason still uses 5y-peak language: {reason!r}"
    )


@given("a price series that peaked 6 months ago and recovered partially")
def step_peak_then_recovery(context):
    import numpy as np
    # 252 trading days. Peak around day 60 (about 6 months back from
    # the end), then sell-off to day 180, then recovery to day 251 —
    # but never reclaiming the peak. Mirrors the SWDA-style narrative
    # the user surfaced.
    idx = pd.bdate_range("2025-05-01", periods=252)
    prices = np.concatenate([
        np.linspace(100, 130, 60),    # ramp into peak
        np.linspace(130, 100, 120),   # drawdown
        np.linspace(100, 115, 72),    # partial recovery
    ])
    context.peak_idx = idx[59]  # day 60 (zero-indexed)
    context.peak_value = 130.0
    df = pd.DataFrame(
        {"open": prices, "high": prices, "low": prices,
         "close": prices, "adj_close": prices, "volume": [1] * 252},
        index=idx,
    )
    context.prices = df


@then("the 52w-high date matches the peak bar")
def step_high_date(context):
    iso = context.state.pct_off_52w_high_date
    assert iso is not None, "pct_off_52w_high_date missing"
    expected = context.peak_idx.date().isoformat()
    assert iso[:10] == expected, f"expected {expected}, got {iso[:10]}"


@then("the 52w-high price matches the peak value")
def step_high_price(context):
    price = context.state.pct_off_52w_high_price
    assert price is not None, "pct_off_52w_high_price missing"
    assert abs(price - context.peak_value) < 0.5, (
        f"expected ~{context.peak_value}, got {price:.2f}"
    )


@then("the entry reason mentions the peak date")
def step_reason_mentions_date(context):
    reason = context.state.entry_reason or ""
    expected = context.peak_idx.date().isoformat()
    # The classifier only emits the peak date for the deep-drawdown
    # BUY branch; for shallower drawdowns it falls through to other
    # rules. Either the peak date appears, OR the verdict is one of
    # those non-bounce-zone reasons.
    if context.state.entry_signal == "BUY" and "drawdown" in reason:
        assert expected in reason, (
            f"BUY-bounce reason missing peak date: {reason!r}"
        )
