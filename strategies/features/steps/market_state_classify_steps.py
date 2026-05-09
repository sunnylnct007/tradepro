"""Steps for market_state_classify.feature — pin the _classify ladder.

Each scenario uses a hand-shaped synthetic series that lands exactly
in the regime under test. The market_state() helper computes the
real metrics from the series; we don't poke private fields. That way
any refactor of the classifier surfaces here without rewriting the
test data.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from behave import given, then, when

from tradepro_strategies.market_state import market_state


def _series(prices: list[float]) -> pd.DataFrame:
    """Wrap a list of prices into the OHLCV-like DataFrame the
    market_state helper expects. Daily business-day index ending
    today; the classifier reads `adj_close` (or `close`) only.

    pandas occasionally returns periods-1 dates from a date_range end
    on a holiday boundary, so we slice both arrays to the shorter
    common length rather than asserting equality."""
    dates = pd.date_range(end=datetime(2026, 5, 9), periods=len(prices), freq="B")
    n = min(len(prices), len(dates))
    return pd.DataFrame(
        {"adj_close": prices[:n], "close": prices[:n]},
        index=dates[:n],
    )


def _build(start: float, peak: float, end: float, *, peak_position: float = 0.75,
           noise: float = 0.05, n: int = 260) -> list[float]:
    """Linear-up to peak, linear-down to end. Adds tiny noise so RSI /
    SMA are computable. Same shape we use elsewhere — keeps tests
    aligned with the range_position fixtures."""
    out: list[float] = []
    for i in range(n):
        t = i / (n - 1)
        if t < peak_position:
            p = start + (peak - start) * (t / peak_position)
        else:
            p = peak - (peak - end) * ((t - peak_position) / (1 - peak_position))
        out.append(p + (-1) ** i * noise)
    return out


@given("a synthetic price series in confirmed downtrend")
def step_downtrend(context):
    # Linear decline from 100 to 60 → -40% drawdown, below SMA200,
    # weak 12m momentum.
    prices = [100 - i * 0.15 + (-1) ** i * 0.05 for i in range(260)]
    context.prices = _series(prices)


@given("a synthetic price series at 52w highs with overbought RSI")
def step_overbought_at_highs(context):
    # Steady climb so RSI stays elevated and the last bar is at the
    # 52w high. EXTENDED_PCT_FROM_HIGH ≤1% AND RSI ≥70 fires WAIT.
    # A small accelerating tail adds RSI strength.
    base = [60 + i * 0.20 for i in range(240)]
    tail = [base[-1] + (j + 1) * 0.40 for j in range(20)]
    context.prices = _series(base + tail)


# Note: "a synthetic VUKE-shaped price series ending at the 70th+
# percentile of its 52w range" is already defined in
# range_position_steps.py and re-used here. Same for "When I compute
# the market state", "Then the entry signal is …", and "Then the
# entry reason mentions …" — behave shares step definitions across
# every file in features/steps so they only need defining once.


@given("a synthetic price series 12% off 52w high with RSI 42 recovering")
def step_bounce_zone(context):
    # Climb to 100, drop to 88 (12% off), then a few small green bars
    # to nudge RSI off the floor.
    climb = [60 + i * 0.20 for i in range(200)]
    drop = [climb[-1] - (i + 1) * 0.30 for i in range(40)]   # to ~88
    bounce = [drop[-1] + (i + 1) * 0.10 for i in range(20)]  # tiny green
    context.prices = _series(climb + drop + bounce)


@given("a synthetic price series with no fresh entry edge")
def step_ambiguous(context):
    # Sideways mid-range, RSI mid, just above SMA. No rule fires →
    # default HOLD branch.
    base = [85 + (i % 5) * 0.10 for i in range(260)]
    context.prices = _series(base)


@given("a synthetic price series in 12% mid-drawdown")
def step_mid_drawdown(context):
    # Climb to 100, drop to 88 — drawdown_from_peak ~-12% (mid zone),
    # 12m mom not weak enough for AVOID, RSI not in bounce zone.
    # Hits the WAIT-on-mid-drawdown branch.
    climb = [50 + i * 0.20 for i in range(220)]
    drop = [climb[-1] - (i + 1) * 0.30 for i in range(40)]
    context.prices = _series(climb + drop)


# `When I compute the market state` and `Then the entry signal is X` /
# `Then the entry reason mentions X` already live in
# range_position_steps.py — behave registers them once and reuses
# them across feature files.
