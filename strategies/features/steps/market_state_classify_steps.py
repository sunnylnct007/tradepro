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


@given("a synthetic price series below SMA200 with RSI bouncing")
def step_below_sma_bouncing(context):
    # Reproduces the BABA / ABBV pattern from the 2026-05-20 reviewer
    # feedback: price below SMA200, positive 12m momentum (so the
    # confirmed-downtrend AVOID doesn't fire), no recent cascade (so
    # the active-crash AVOID doesn't fire), meaningful drawdown off
    # the 52w high, RSI bouncing in the 50s.
    #
    # Geometry: climb gently from a low base over ~200 bars to build
    # a moderate SMA200, then a fast >20% drop in ~40 bars (forces
    # price below SMA200 because SMA200 still reflects the climb),
    # then a small bounce so RSI lifts off the floor. 12m return
    # remains positive because the starting point was much lower.
    climb = [40 + i * 0.30 for i in range(200)]      # 40 → 100, builds SMA
    drop = [climb[-1] - (i + 1) * 0.55 for i in range(40)]  # 100 → ~78
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


@given("a synthetic price series in active 10d crash below SMA200")
def step_active_crash(context):
    # Build a series that:
    #   - climbs gently for ~230 bars (so SMA200 sits around mid-range)
    #   - drifts sideways for ~20 bars (so SMA200 catches up near the
    #     current price)
    #   - then drops ~12% over the LAST 10 bars (well past the
    #     ACTIVE_CRASH_10D_PCT = -8% threshold) AND ends below SMA200.
    # That hits the new AVOID-active-crash branch, which must fire
    # BEFORE the bounce-zone BUY rule (the -12% drop from a recent
    # peak would otherwise satisfy MEANINGFUL_52W_DROP_PCT and RSI
    # could end in the bounce zone).
    climb = [80 + i * 0.05 for i in range(230)]  # 80 -> ~91.5
    flat = [climb[-1] + (-1) ** i * 0.02 for i in range(20)]
    pre_crash = flat[-1]
    crash = [pre_crash * (1.0 - 0.013 * (i + 1)) for i in range(10)]  # ~-12% over 10 bars
    context.prices = _series(climb + flat + crash)


# `When I compute the market state` and `Then the entry signal is X` /
# `Then the entry reason mentions X` already live in
# range_position_steps.py — behave registers them once and reuses
# them across feature files.
