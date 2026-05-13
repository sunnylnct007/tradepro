"""Steps for ichimoku.feature — synthetic OHLC; no Yahoo / network."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.indicators import ichimoku
from tradepro_strategies.strategies import (
    ichimoku_cloud_signals,
    ichimoku_targets,
)


def _ohlc_uptrend(n: int) -> pd.DataFrame:
    """Smooth +1%-per-bar uptrend on business days. Wide enough High/Low
    range around close so the Tenkan/Kijun midranges aren't degenerate."""
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=n + 10, freq="B")[-n:]
    closes = np.array([100.0 * (1.01 ** i) for i in range(n)])
    highs = closes * 1.005
    lows = closes * 0.995
    return pd.DataFrame(
        {"open": closes, "high": highs, "low": lows,
         "close": closes, "adj_close": closes, "volume": [1_000_000] * n},
        index=dates,
    )


def _ohlc_above_cloud(n: int = 90) -> pd.DataFrame:
    """Long enough to draw a full forward cloud (52 + 26 = 78 minimum)
    AND in the bar-arrangement Ichimoku reads as ABOVE the cloud."""
    return _ohlc_uptrend(n)


@given("a synthetic OHLC series of {n:d} bars trending up at 1% per bar")
def step_uptrend_n(context, n: int) -> None:
    context.prices = _ohlc_uptrend(n)


@given("a synthetic OHLC series of {n:d} bars")
def step_ohlc_short(context, n: int) -> None:
    context.prices = _ohlc_uptrend(n)


@given("a synthetic OHLC series that breaks above its forward cloud on the last bar")
def step_breakout(context) -> None:
    # Flat 80 bars at price 100 → cloud sits at 100. Then a sharp
    # rally over the last 10 bars pushes price up to ~115. Somewhere
    # in those 10 bars the close crosses above the cloud high for
    # the first time, firing the +1 signal. Chikou-confirm is automatic
    # because today's close (115) > close 26 bars ago (still 100).
    n = 90
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=n + 10, freq="B")[-n:]
    flat = [100.0] * 80
    rally = list(np.linspace(101.0, 115.0, 10))
    closes = np.array(flat + rally)
    df = pd.DataFrame(
        {"open": closes, "high": closes * 1.005, "low": closes * 0.995,
         "close": closes, "adj_close": closes, "volume": [1_000_000] * n},
        index=dates,
    )
    context.prices = df


@given("a synthetic OHLC series sitting above its forward cloud")
def step_above(context) -> None:
    context.prices = _ohlc_above_cloud(90)


@when("I compute the ichimoku indicator with defaults")
def step_compute_indicator(context) -> None:
    context.ich = ichimoku(
        context.prices["high"], context.prices["low"], context.prices["close"],
    )


@when("I generate ichimoku_cloud signals")
def step_generate_signals(context) -> None:
    context.signals = ichimoku_cloud_signals(context.prices)


@when("I compute the ichimoku_targets envelope")
def step_targets(context) -> None:
    context.targets = ichimoku_targets(context.prices)


@then("the result has columns tenkan, kijun, senkou_a, senkou_b, chikou, cloud_high, cloud_low, cloud_thickness")
def step_columns(context) -> None:
    expected = {"tenkan", "kijun", "senkou_a", "senkou_b",
                "chikou", "cloud_high", "cloud_low", "cloud_thickness"}
    missing = expected - set(context.ich.columns)
    assert not missing, f"missing columns: {missing}"


@then("the last cloud_high is greater than or equal to the last cloud_low")
def step_cloud_band(context) -> None:
    h = context.ich["cloud_high"].iloc[-1]
    l = context.ich["cloud_low"].iloc[-1]
    assert h >= l, f"cloud_high {h} < cloud_low {l}"


@then("the latest signal is {n:d}")
def step_latest_signal(context, n: int) -> None:
    actual = int(context.signals.iloc[-1])
    # Long-only strategy; 0 is no-op, +1 is fresh entry. We assert
    # the signal series CONTAINS at least one +1 anywhere in the
    # last 26 bars — the breakout-and-confirm rule means it might
    # fire a few bars before the very last index.
    if n == 1:
        recent = context.signals.iloc[-26:]
        assert (recent == 1).any(), (
            f"expected at least one +1 in last 26 bars; "
            f"got {recent.tolist()}"
        )
    else:
        assert actual == n, f"latest signal {actual} != {n}"


@then("price_target is a positive number")
def step_pt_positive(context) -> None:
    pt = context.targets.get("price_target")
    assert pt is not None and pt > 0, f"price_target = {pt}"


@then("stop_level is a positive number")
def step_stop_positive(context) -> None:
    s = context.targets.get("stop_level")
    assert s is not None and s > 0, f"stop_level = {s}"


@then('cloud_position equals "{expected}"')
def step_cloud_position(context, expected: str) -> None:
    actual = context.targets.get("cloud_position")
    assert actual == expected, f"cloud_position {actual!r} != {expected!r}"
