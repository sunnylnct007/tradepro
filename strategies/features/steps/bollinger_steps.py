"""Steps for bollinger.feature — synthetic OHLC, no Yahoo / network."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.indicators import bollinger
from tradepro_strategies.strategies import bollinger_bounce_signals


def _close_series(n: int) -> pd.DataFrame:
    """Closing-only series with a tiny sinusoidal wiggle — non-flat
    enough to give bollinger a non-zero stdev."""
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=n + 10, freq="B")[-n:]
    closes = 100.0 + np.sin(np.linspace(0, 8 * np.pi, n))
    return pd.DataFrame(
        {"close": closes, "adj_close": closes,
         "high": closes * 1.005, "low": closes * 0.995,
         "open": closes, "volume": [1_000_000] * n},
        index=dates,
    )


def _crash_then_settle(n_pre: int = 40, n_crash: int = 5) -> pd.DataFrame:
    """40 bars at 100, then 5 bars crashing to 60. The post-crash
    closes are well outside the (still ~100, narrow) bollinger band
    drawn from the 20-bar window that mostly contained the flat
    series — guaranteed AT_LOWER + oversold RSI."""
    n = n_pre + n_crash
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=n + 10, freq="B")[-n:]
    pre = [100.0] * n_pre
    crash = list(np.linspace(95.0, 60.0, n_crash))
    closes = np.array(pre + crash)
    return pd.DataFrame(
        {"close": closes, "adj_close": closes,
         "high": closes * 1.001, "low": closes * 0.999,
         "open": closes, "volume": [1_000_000] * n},
        index=dates,
    )


@given("a synthetic closing series of {n:d} bars varying around 100")
def step_closing_series(context, n: int) -> None:
    context.prices = _close_series(n)


@given("a synthetic OHLC series sitting at 60 with the prior 40 bars at 100")
def step_crash(context) -> None:
    context.prices = _crash_then_settle()


@when("I compute the bollinger indicator")
def step_compute_bollinger(context) -> None:
    context.bb = bollinger(context.prices["close"])


@when("I generate bollinger_bounce signals")
def step_generate_bounce(context) -> None:
    context.signals = bollinger_bounce_signals(context.prices)


@when("I generate bollinger_bounce signals on the same series as OHLC")
def step_generate_bounce_ohlc(context) -> None:
    context.signals = bollinger_bounce_signals(context.prices)


@then("the result has columns middle, upper, lower, bandwidth, percent_b")
def step_columns(context) -> None:
    expected = {"middle", "upper", "lower", "bandwidth", "percent_b"}
    missing = expected - set(context.bb.columns)
    assert not missing, f"missing columns: {missing}"


@then("the last upper is greater than the last middle")
def step_upper_above_middle(context) -> None:
    u = context.bb["upper"].iloc[-1]
    m = context.bb["middle"].iloc[-1]
    assert u > m, f"upper {u} not > middle {m}"


@then("the last lower is less than the last middle")
def step_lower_below_middle(context) -> None:
    l = context.bb["lower"].iloc[-1]
    m = context.bb["middle"].iloc[-1]
    assert l < m, f"lower {l} not < middle {m}"


@then('bollinger_position is "{expected}"')
def step_bp(context, expected: str) -> None:
    actual = context.state.bollinger_position
    assert actual == expected, f"bollinger_position {actual!r} != {expected!r}"


@then("the signal series contains at least one +1")
def step_has_plus_one(context) -> None:
    assert (context.signals == 1).any(), (
        f"no +1 in signals: {context.signals.tolist()}"
    )


@then("the signal series has no +1")
def step_no_plus_one(context) -> None:
    assert not (context.signals == 1).any(), (
        f"unexpected +1 in: {context.signals[context.signals == 1].tolist()}"
    )
