"""Steps for range_position.feature — pin the VUKE-class downgrade."""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from behave import given, then, when

from tradepro_strategies.market_state import market_state


def _build_series(
    start: float, peak: float, end: float, *, peak_position: float = 0.75,
) -> pd.DataFrame:
    """Construct a 260-bar adj_close series that runs from `start` up
    to `peak` over the first `peak_position` of the year, then drifts
    to `end`. Adds tiny per-bar noise so RSI / SMA are computable."""
    dates = pd.date_range(end=datetime(2026, 5, 8), periods=260, freq='B')
    prices = []
    for i in range(260):
        t = i / 260
        if t < peak_position:
            p = start + (peak - start) * (t / peak_position)
        else:
            p = peak - (peak - end) * ((t - peak_position) / (1 - peak_position))
        prices.append(p + (-1) ** i * 0.05)
    return pd.DataFrame({'adj_close': prices, 'close': prices}, index=dates)


@given("a synthetic VUKE-shaped price series ending at the 70th+ percentile of its 52w range")
def step_vuke_series(context):
    # low ≈ 36.83, high ≈ 47.73, end ≈ 44.74 → ~71st percentile.
    context.prices = _build_series(36.83, 47.73, 44.74)


@given("a synthetic price series ending at the 50th percentile of its 52w range")
def step_mid_series(context):
    # low 30, high 50, end 40 → exactly 50th percentile of (30, 50).
    context.prices = _build_series(30.0, 50.0, 40.0)


@given("a synthetic recovering price series ending at the 30th percentile of its 52w range")
def step_low_series(context):
    # low 30, high 50, end 36 → 30th percentile.
    context.prices = _build_series(30.0, 50.0, 36.0)


@when("I compute the market state")
def step_compute(context):
    context.state = market_state("TEST", context.prices)


@then('the entry signal is "{expected}"')
def step_signal_eq(context, expected: str):
    actual = context.state.entry_signal
    assert actual == expected, f"expected {expected!r}, got {actual!r}"


@then('the entry reason mentions "{snippet}"')
def step_reason_mentions(context, snippet: str):
    reason = context.state.entry_reason or ""
    assert snippet in reason, (
        f"reason missing {snippet!r}: {reason!r}"
    )


@then('the decision trace contains a "{name}" row with status "{status}"')
def step_trace_row(context, name: str, status: str):
    matches = [
        r for r in context.state.decision_trace
        if r.get("name") == name
    ]
    assert matches, (
        f"no trace row named {name!r}; "
        f"got: {[r.get('name') for r in context.state.decision_trace]}"
    )
    actual = matches[0].get("status")
    assert actual == status, (
        f"row {name!r}: expected status={status!r}, got {actual!r} "
        f"(detail: {matches[0].get('detail')!r})"
    )
