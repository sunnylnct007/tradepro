"""Step implementations for ibkr_bar_provider.feature.

All scenarios use IBKRProvider(_fetch_fn=...) — no real TWS / IB Gateway.
The mock captures every call so we can assert chunk count, bar_size,
durationStr, and whatToShow without touching the network.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest
from behave import given, then, when

from tradepro_strategies.bar_cache.errors import (
    BarFetchError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
)
from tradepro_strategies.bar_cache.providers import get_provider
from tradepro_strategies.bar_cache.providers.base import (
    _clear_registry_for_tests,
    register_provider,
)
from tradepro_strategies.bar_cache.providers.ibkr_provider import (
    IBKRProvider,
)


# ─── Date constants ─────────────────────────────────────────────────

_ONE_DAY_START = datetime(2024, 12, 23, tzinfo=timezone.utc)
_ONE_DAY_END   = datetime(2024, 12, 24, tzinfo=timezone.utc)
_ONE_MONTH_START = datetime(2024, 12, 2, tzinfo=timezone.utc)
_ONE_MONTH_END   = datetime(2024, 12, 31, 23, tzinfo=timezone.utc)
_35D_START = datetime(2024, 11, 18, tzinfo=timezone.utc)
_35D_END   = datetime(2024, 12, 23, tzinfo=timezone.utc)


# ─── Mock factory helpers ────────────────────────────────────────────

def _make_bars(n: int, start: datetime | None = None) -> list[dict]:
    """Return n well-formed bar dicts with ascending 1-minute timestamps."""
    base = start or _ONE_DAY_START
    return [
        {
            "timestamp": base + timedelta(minutes=i),
            "open":  100.0 + i * 0.01,
            "high":  101.0 + i * 0.01,
            "low":   99.0  + i * 0.01,
            "close": 100.5 + i * 0.01,
            "volume": 1000 + i,
        }
        for i in range(n)
    ]


class _CapturingMock:
    """Records every _fetch_chunk call; returns configurable bars."""

    def __init__(self, bars_per_call: int | list[list[dict]] | None = None,
                 raise_on: str | None = None):
        self.calls: list[dict] = []
        self._bars_per_call = bars_per_call  # int → same count every call
        self._raise_on = raise_on

    def __call__(
        self,
        symbol: str,
        bar_size: str,
        end_dt: datetime,
        duration_str: str,
        what_to_show: str,
        use_rth: bool,
    ) -> list[dict]:
        self.calls.append({
            "symbol": symbol,
            "bar_size": bar_size,
            "end_dt": end_dt,
            "duration_str": duration_str,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
        })
        if self._raise_on == "connection":
            raise ProviderNetworkError(
                provider="ibkr", canonical=symbol,
                message="mock: cannot connect to TWS",
            )
        if self._raise_on == "pacing":
            raise ProviderRateLimitError(
                provider="ibkr", canonical=symbol,
                message="mock: pacing violation",
            )

        call_idx = len(self.calls) - 1
        if isinstance(self._bars_per_call, list):
            # Caller supplied per-call bar lists
            if call_idx < len(self._bars_per_call):
                return self._bars_per_call[call_idx]
            return []
        # Constant count — anchor timestamps to the chunk's end_dt so they
        # always fall within the requested [start, end] window regardless of
        # what date constants the test uses for start/end.
        n = int(self._bars_per_call or 0)
        bars_start = end_dt - timedelta(minutes=n)
        return _make_bars(n, start=bars_start)


# ─── Background step ────────────────────────────────────────────────


@given("the IBKRProvider registry is cleared for a clean test")
def step_clear_registry(context) -> None:
    _clear_registry_for_tests()
    context.fetch_error = None
    context.fetch_result = None
    context.mock = None
    context.provider = None


# ─── Given — provider construction ──────────────────────────────────


@given("an IBKRProvider with a mock that returns {n:d} bars")
def step_provider_mock_n_bars(context, n: int) -> None:
    context.mock = _CapturingMock(bars_per_call=n)
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given("an IBKRProvider with a mock that returns {n:d} bars per chunk")
def step_provider_mock_n_bars_per_chunk(context, n: int) -> None:
    context.mock = _CapturingMock(bars_per_call=n)
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given("an IBKRProvider with a mock that returns 5 well-formed bars")
def step_provider_mock_5_bars(context) -> None:
    context.mock = _CapturingMock(bars_per_call=5)
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given('an IBKRProvider with what_to_show="{wts}" and a mock returning {n:d} bars')
def step_provider_mock_wts(context, wts: str, n: int) -> None:
    context.mock = _CapturingMock(bars_per_call=n)
    context.provider = IBKRProvider(_fetch_fn=context.mock, what_to_show=wts)


@given("an IBKRProvider with a mock returning ascending timestamps across 2 chunks")
def step_provider_mock_ascending_2chunks(context) -> None:
    # Chunk 1: days 0..6, Chunk 2: days 7..34
    chunk1 = _make_bars(10, start=_35D_START)
    chunk2 = _make_bars(10, start=_35D_START + timedelta(days=30))
    context.mock = _CapturingMock(bars_per_call=[chunk1, chunk2])
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given("an IBKRProvider with a mock that returns an overlapping bar at the chunk boundary")
def step_provider_mock_overlap(context) -> None:
    # Last bar of chunk 1 == first bar of chunk 2 → dedup must remove one
    shared_ts = _35D_START + timedelta(days=7)
    chunk1 = _make_bars(5, start=_35D_START) + [
        {
            "timestamp": shared_ts,
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 999,
        }
    ]
    chunk2 = [
        {
            "timestamp": shared_ts,
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.5, "volume": 999,
        }
    ] + _make_bars(5, start=shared_ts + timedelta(minutes=1))
    context.mock = _CapturingMock(bars_per_call=[chunk1, chunk2])
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given("an IBKRProvider whose mock raises a connection error")
def step_provider_mock_connection_error(context) -> None:
    context.mock = _CapturingMock(raise_on="connection")
    context.provider = IBKRProvider(_fetch_fn=context.mock)


@given("an IBKRProvider whose mock raises a pacing violation")
def step_provider_mock_pacing(context) -> None:
    context.mock = _CapturingMock(raise_on="pacing")
    context.provider = IBKRProvider(_fetch_fn=context.mock)


# ─── When — fetch ────────────────────────────────────────────────────


@when('I fetch "{sym}" {asset_class} 1m bars for one day')
def step_fetch_1m_one_day(context, sym: str, asset_class: str) -> None:
    _do_fetch(context, sym, asset_class, "1m", _ONE_DAY_START, _ONE_DAY_END)


@when('I fetch "{sym}" {asset_class} 5m bars for one day')
def step_fetch_5m_one_day(context, sym: str, asset_class: str) -> None:
    _do_fetch(context, sym, asset_class, "5m", _ONE_DAY_START, _ONE_DAY_END)


@when('I fetch "{sym}" {asset_class} 1d bars for one month')
def step_fetch_1d_one_month(context, sym: str, asset_class: str) -> None:
    _do_fetch(context, sym, asset_class, "1d", _ONE_MONTH_START, _ONE_MONTH_END)


@when('I fetch "{sym}" {asset_class} "{resolution}" bars for one day')
def step_fetch_custom_resolution_one_day(
    context, sym: str, asset_class: str, resolution: str,
) -> None:
    _do_fetch(context, sym, asset_class, resolution, _ONE_DAY_START, _ONE_DAY_END)


@when('I fetch "{sym}" {asset_class} 1m bars for 35 days')
def step_fetch_1m_35days(context, sym: str, asset_class: str) -> None:
    _do_fetch(context, sym, asset_class, "1m", _35D_START, _35D_END)


def _do_fetch(
    context, sym: str, asset_class: str, resolution: str,
    start: datetime, end: datetime,
) -> None:
    context.fetch_error = None
    context.fetch_result = None
    try:
        df, meta = context.provider.fetch(sym, asset_class, resolution, start, end)
        context.fetch_result = df
        context.fetch_meta   = meta
    except BarFetchError as exc:
        context.fetch_error = exc


@when("I import the bar_cache providers module")
def step_import_providers(context) -> None:
    # Re-import triggers auto-registration. Must delete the cached module
    # entry so Python re-executes the module body (including the
    # register_provider(IBKRProvider()) line at the bottom of the file),
    # because importlib.reload() on the package __init__ doesn't re-execute
    # already-cached sub-modules.
    import importlib
    import sys
    for name in list(sys.modules):
        if "ibkr_provider" in name:
            del sys.modules[name]
    import tradepro_strategies.bar_cache.providers.ibkr_provider  # noqa: F401


# ─── Then — assertions ───────────────────────────────────────────────


@then('the mock received barSizeSetting "{expected}"')
def step_assert_bar_size(context, expected: str) -> None:
    assert context.mock.calls, "no calls recorded on the mock"
    actual = context.mock.calls[0]["bar_size"]
    assert actual == expected, f"barSizeSetting: expected {expected!r} got {actual!r}"


@then('the mock received whatToShow "{expected}"')
def step_assert_what_to_show(context, expected: str) -> None:
    assert context.mock.calls, "no calls recorded on the mock"
    actual = context.mock.calls[0]["what_to_show"]
    assert actual == expected, f"whatToShow: expected {expected!r} got {actual!r}"


@then("the mock was called exactly {n:d} time")
@then("the mock was called exactly {n:d} times")
def step_assert_call_count(context, n: int) -> None:
    actual = len(context.mock.calls)
    assert actual == n, (
        f"mock call count: expected {n} got {actual}. "
        f"calls={context.mock.calls}"
    )


@then("the result has {n:d} rows")
def step_assert_row_count(context, n: int) -> None:
    assert context.fetch_error is None, (
        f"unexpected fetch error: {context.fetch_error}"
    )
    actual = len(context.fetch_result)
    assert actual == n, (
        f"row count: expected {n} got {actual}"
    )


@then("the result has {n:d} rows and no error is raised")
def step_assert_row_count_no_error(context, n: int) -> None:
    assert context.fetch_error is None, (
        f"unexpected fetch error: {context.fetch_error}"
    )
    actual = len(context.fetch_result)
    assert actual == n, f"expected 0 rows, got {actual}"


@then("the returned DataFrame index is strictly increasing")
def step_assert_strictly_increasing(context) -> None:
    df = context.fetch_result
    assert len(df) > 0, "result is empty"
    diffs = df.index.to_series().diff().dropna()
    assert (diffs > timedelta(0)).all(), (
        f"index is not strictly increasing: first violation at "
        f"{diffs[diffs <= timedelta(0)].index[0]}"
    )


@then("the result contains no duplicate timestamps")
def step_assert_no_dupes(context) -> None:
    df = context.fetch_result
    dupes = df.index[df.index.duplicated()]
    assert len(dupes) == 0, f"duplicate timestamps: {list(dupes)}"


@then("the result columns include open, high, low, close, volume")
def step_assert_columns(context) -> None:
    df = context.fetch_result
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns, f"missing column {col!r}; got {list(df.columns)}"


@then("the result has adj_factor column equal to 1.0 for all rows")
def step_assert_adj_factor(context) -> None:
    df = context.fetch_result
    assert "adj_factor" in df.columns, "adj_factor column missing"
    assert (df["adj_factor"] == 1.0).all(), "adj_factor is not 1.0 everywhere"


@then('the result has source column equal to "{expected}" for all rows')
def step_assert_source(context, expected: str) -> None:
    df = context.fetch_result
    assert "source" in df.columns, "source column missing"
    bad = df[df["source"] != expected]
    assert bad.empty, f"source != {expected!r} in rows: {bad.index.tolist()}"


@then("the result index is tz-aware UTC")
def step_assert_utc_index(context) -> None:
    df = context.fetch_result
    assert df.index.tz is not None, "index is tz-naive"
    tz_str = str(df.index.tz)
    assert "UTC" in tz_str or tz_str == "UTC", (
        f"index tz is {tz_str!r}, expected UTC"
    )


@then("a ProviderParseError is raised")
def step_assert_parse_error(context) -> None:
    assert isinstance(context.fetch_error, ProviderParseError), (
        f"expected ProviderParseError, got {context.fetch_error!r}"
    )


@then('a ProviderNetworkError is raised with provider "{expected_provider}"')
def step_assert_network_error(context, expected_provider: str) -> None:
    assert isinstance(context.fetch_error, ProviderNetworkError), (
        f"expected ProviderNetworkError, got {context.fetch_error!r}"
    )
    assert context.fetch_error.provider == expected_provider, (
        f"provider: expected {expected_provider!r} "
        f"got {context.fetch_error.provider!r}"
    )


@then('a ProviderRateLimitError is raised with provider "{expected_provider}"')
def step_assert_rate_limit_error(context, expected_provider: str) -> None:
    assert isinstance(context.fetch_error, ProviderRateLimitError), (
        f"expected ProviderRateLimitError, got {context.fetch_error!r}"
    )
    assert context.fetch_error.provider == expected_provider, (
        f"provider: expected {expected_provider!r} "
        f"got {context.fetch_error.provider!r}"
    )


@then('the max_history for resolution "{resolution}" is {days:d} days')
def step_assert_max_history_days(context, resolution: str, days: int) -> None:
    p = context.provider
    mh = p.max_history(resolution)
    assert mh == timedelta(days=days), (
        f"max_history({resolution!r}): expected {days}d got {mh}"
    )


@then('the max_history for resolution "{resolution}" is None')
def step_assert_max_history_none(context, resolution: str) -> None:
    p = context.provider
    mh = p.max_history(resolution)
    assert mh is None, (
        f"max_history({resolution!r}): expected None got {mh}"
    )


@then('the provider does not support resolution "{resolution}"')
def step_assert_not_supported(context, resolution: str) -> None:
    p = context.provider
    assert not p.supports_resolution(resolution), (
        f"expected {resolution!r} to be unsupported but supports_resolution returned True"
    )


@then("the provider supports resolutions 1m, 5m, 15m, 30m, 1h, 1d")
def step_assert_standard_resolutions(context) -> None:
    p = context.provider
    for res in ("1m", "5m", "15m", "30m", "1h", "1d"):
        assert p.supports_resolution(res), (
            f"provider should support {res!r} but supports_resolution returned False"
        )


@then('"ibkr" is resolvable via get_provider')
def step_assert_registry(context) -> None:
    provider = get_provider("ibkr")
    # Use .name attribute (not isinstance) — the module was reloaded so the
    # class objects differ even though the source is identical.
    assert getattr(provider, "name", None) == "ibkr", (
        f"expected provider.name='ibkr', got type={type(provider).__name__} "
        f"name={getattr(provider, 'name', '<missing>')!r}"
    )
