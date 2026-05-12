"""Steps for earnings.feature — exercises the Family-4 signal with
fully synthetic earnings + price data. No yfinance network calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from behave import given, then, when
from behave.matchers import use_step_matcher

from tradepro_strategies.earnings import (
    beat_and_retreat_signal,
    earnings_trace_row,
    fetch_earnings_in_range,
)

# `{pct:f}` would have required a literal decimal point in the
# matched scenario text — so "surprise 5%" wouldn't match but
# "surprise 5.0%" would. `:g` is the general-number type that
# accepts both. Picked here so scenario authors don't have to
# remember to write 5.0 instead of 5.
use_step_matcher("parse")


class _FakeTicker:
    """yfinance.Ticker stand-in that returns a pre-baked DataFrame."""
    def __init__(self, df: pd.DataFrame):
        self.earnings_dates = df


def _ticker_factory(df: pd.DataFrame):
    return lambda symbol: _FakeTicker(df)


def _earnings_df(days_ago: int, surprise_pct: float, *, has_eps_actual: bool = True):
    """One-row earnings_dates DataFrame matching the yfinance shape."""
    when = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_ago)
    estimate = 1.0
    if has_eps_actual:
        # Construct an actual that matches the surprise percentage.
        actual = estimate * (1 + surprise_pct / 100.0)
    else:
        actual = float("nan")
    return pd.DataFrame(
        {"EPS Estimate": [estimate], "Reported EPS": [actual],
         "Surprise(%)": [surprise_pct]},
        index=pd.DatetimeIndex([when], name="Earnings Date"),
    )


def _price_series_with_retreat(announce_date: pd.Timestamp, retreat_pct: float):
    """Build prices that peaked shortly after the announcement and
    retreated by `retreat_pct` to the most recent bar. retreat_pct
    is negative for a real retreat (e.g. -8.0)."""
    days_after = (datetime.now(timezone.utc).replace(tzinfo=None) - announce_date.tz_convert("UTC").tz_localize(None)).days
    days_after = max(days_after, 3)
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days_after + 5)
    n = len(idx)
    # Rally up to ~70% mark, then retreat to retreat_pct from peak.
    peak = 110.0
    floor = peak * (1 + retreat_pct / 100.0)  # retreat_pct=-8 → floor=101.2
    pre = np.linspace(100.0, peak, max(2, n // 2))
    post = np.linspace(peak, floor, n - len(pre))
    closes = np.concatenate([pre, post])
    df = pd.DataFrame(
        {"open": closes, "high": closes, "low": closes,
         "close": closes, "adj_close": closes, "volume": [1] * n},
        index=idx,
    )
    return df


def _price_series_kept_rallying(announce_date):
    days_after = (datetime.now(timezone.utc).replace(tzinfo=None) - announce_date.tz_convert("UTC").tz_localize(None)).days
    days_after = max(days_after, 3)
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=days_after + 5)
    n = len(idx)
    closes = np.linspace(100.0, 120.0, n)
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes,
         "close": closes, "adj_close": closes, "volume": [1] * n},
        index=idx,
    )


@given("an earnings beat {days_ago:d} days ago with surprise {pct:g}%")
def step_beat(context, days_ago: int, pct: float):
    context.earnings_df = _earnings_df(days_ago, pct)
    context.announce = context.earnings_df.index[0]


@given("an earnings miss {days_ago:d} days ago with surprise -{pct:g}%")
def step_miss(context, days_ago: int, pct: float):
    context.earnings_df = _earnings_df(days_ago, -pct)
    context.announce = context.earnings_df.index[0]


@given("no earnings within the last {n:d} days")
def step_no_recent(context, n: int):
    # Empty earnings DataFrame — yfinance returns this when no recent data
    context.earnings_df = pd.DataFrame(
        {"EPS Estimate": [], "Reported EPS": [], "Surprise(%)": []}
    )
    context.announce = None


@given("post-earnings prices that retreated {pct:g}% from peak")
def step_retreat(context, pct: float):
    context.prices = _price_series_with_retreat(context.announce, -pct)


@given("post-earnings prices that kept rallying")
def step_keep_rallying(context):
    context.prices = _price_series_kept_rallying(context.announce)


@when("I evaluate the beat-and-retreat signal")
def step_evaluate(context):
    prices = getattr(context, "prices", pd.DataFrame())
    context.signal = beat_and_retreat_signal(
        "TEST", prices, ticker_factory=_ticker_factory(context.earnings_df),
    )


@then('the verdict is "{expected}"')
def step_verdict(context, expected: str):
    assert context.signal["verdict"] == expected, context.signal


@then("fired is {expected}")
def step_fired(context, expected: str):
    expected_bool = {"True": True, "False": False}[expected]
    assert context.signal["fired"] is expected_bool, context.signal


@then("days_remaining_in_window is at least {n:d}")
def step_days_remaining(context, n: int):
    actual = context.signal["days_remaining_in_window"]
    assert actual is not None and actual >= n, context.signal


# ---- Trace row formatting ----

@given("a STRONG beat-and-retreat signal envelope")
def step_strong_envelope(context):
    context.signal = {
        "_source": "live://earnings/TEST",
        "fired": True,
        "verdict": "STRONG",
        "earnings": {"symbol": "TEST", "announce_date": "2026-04-25",
                     "eps_estimate": 1.0, "eps_actual": 1.05,
                     "surprise_pct": 5.0, "beat": True},
        "days_since_earnings": 9,
        "days_remaining_in_window": 51,
        "post_earnings_peak": 110.0, "current_price": 102.0,
        "retreat_from_post_earnings_peak_pct": -7.3,
    }


@given("a STRONG beat-and-retreat signal envelope with upcoming earnings in {days:d} days")
def step_strong_envelope_with_upcoming(context, days: int):
    step_strong_envelope(context)
    context.signal["upcoming"] = {
        "date": "2026-08-12",
        "days_until": days,
        "hour": "amc",
    }


@given("a NO_RECENT envelope with upcoming earnings in {days:d} days")
def step_no_recent_with_upcoming(context, days: int):
    context.signal = {
        "_source": "live://earnings/TEST",
        "fired": False,
        "verdict": "NO_RECENT",
        "earnings": None,
        "upcoming": {
            "date": "2026-08-12",
            "days_until": days,
            "hour": "bmo",
        },
    }


@when("I build the earnings trace row")
def step_build_trace(context):
    context.trace_row = earnings_trace_row(context.signal)


@then('the trace status is "{expected}"')
def step_trace_status(context, expected: str):
    assert context.trace_row is not None
    assert context.trace_row["status"] == expected, context.trace_row


@then('the trace detail mentions "{snippet}"')
def step_trace_detail(context, snippet: str):
    assert context.trace_row is not None
    assert snippet in context.trace_row["detail"], context.trace_row


# ---- Historical earnings overlay (chart markers) ----

def _multi_earnings_df(days_ago_list, *, include_future: bool = False):
    """Build a yfinance-shaped earnings_dates DataFrame with one row per
    days-ago entry. When `include_future` is True a sentinel row 30 days
    in the future (NaN Reported EPS) is prepended — this mirrors how
    yfinance returns not-yet-reported earnings."""
    rows: list[dict] = []
    index: list[pd.Timestamp] = []
    if include_future:
        rows.append({"EPS Estimate": 1.0, "Reported EPS": float("nan"),
                     "Surprise(%)": float("nan")})
        index.append(pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=30))
    for days_ago in days_ago_list:
        rows.append({"EPS Estimate": 1.0, "Reported EPS": 1.05,
                     "Surprise(%)": 5.0})
        index.append(pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days_ago))
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="Earnings Date"))


@given("a yfinance earnings_dates frame with reports at 30, 120 and 365 days ago")
def step_multi_earnings(context):
    context.earnings_df = _multi_earnings_df([30, 120, 365])


@given("a yfinance earnings_dates frame with reports at 100 and 4000 days ago")
def step_earnings_outside_window(context):
    context.earnings_df = _multi_earnings_df([100, 4000])


@given("a yfinance earnings_dates frame with a future row and one report 30 days ago")
def step_earnings_with_future(context):
    context.earnings_df = _multi_earnings_df([30], include_future=True)


@given("a yfinance ticker that raises on earnings_dates")
def step_earnings_raises(context):
    class _ExplodingTicker:
        @property
        def earnings_dates(self):
            raise RuntimeError("yfinance is sad today")
    context.ticker_factory = lambda symbol: _ExplodingTicker()


@when("I call fetch_earnings_in_range with a 5-year lookback")
def step_call_history(context):
    factory = getattr(context, "ticker_factory", None)
    if factory is None:
        factory = _ticker_factory(context.earnings_df)
    context.history = fetch_earnings_in_range(
        "TEST", lookback_days=1825, ticker_factory=factory,
    )


@then("the result has {n:d} entries")
def step_history_count(context, n: int):
    assert len(context.history) == n, context.history


@then("the result has {n:d} entry")
def step_history_count_singular(context, n: int):
    assert len(context.history) == n, context.history


@then("the result is empty")
def step_history_empty(context):
    assert context.history == [], context.history


@then("the entries are sorted oldest-first")
def step_history_sorted(context):
    dates = [e["date"] for e in context.history]
    assert dates == sorted(dates), dates


@then("each entry has a date, surprise_pct and eps_actual")
def step_history_shape(context):
    for entry in context.history:
        assert "date" in entry and entry["date"]
        assert "surprise_pct" in entry
        assert "eps_actual" in entry
