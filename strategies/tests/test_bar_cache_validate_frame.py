"""Regression coverage for `UsEtfPlugin.validate_frame`'s isolated-spike guard,
added 2026-08-08 after auditing the NEW bar_cache system (now primary for
ichimoku_equity) and finding it had NaN protection but no equivalent to the
legacy cache.py holiday-phantom-spike guard (VLUE-style: absurd price, tiny
volume, on a real market holiday). A bad frame is now rejected outright
(ProviderParseError) so the caller falls through to the next provider in the
chain instead of silently caching a spike."""
from __future__ import annotations

import pandas as pd
import pytest

from tradepro_strategies.bar_cache.asset_classes.us_etf import UsEtfPlugin
from tradepro_strategies.bar_cache.errors import ProviderParseError

_PLUGIN = UsEtfPlugin()


def _frame(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1000] * len(closes), "adj_factor": [1.0] * len(closes),
         "source": ["test"] * len(closes)},
        index=idx,
    )


def test_isolated_spike_bar_is_rejected():
    df = _frame([10.0, 11.0, 200.0, 13.0, 14.0])  # >4x both neighbours
    with pytest.raises(ProviderParseError, match="price-spike"):
        _PLUGIN.validate_frame(df)


def test_isolated_crash_bar_is_rejected():
    df = _frame([10.0, 11.0, 0.5, 13.0, 14.0])  # <0.25x both neighbours
    with pytest.raises(ProviderParseError, match="price-spike"):
        _PLUGIN.validate_frame(df)


def test_genuine_rally_is_not_rejected():
    df = _frame([10.0, 11.0, 40.0, 41.0, 42.0])  # sustained, not isolated
    _PLUGIN.validate_frame(df)  # must not raise


def test_clean_frame_is_not_rejected():
    df = _frame([10.0, 11.0, 12.0, 13.0, 14.0])
    _PLUGIN.validate_frame(df)  # must not raise


def test_nan_close_still_rejected_by_the_pre_existing_guard():
    df = _frame([10.0, 11.0, float("nan"), 13.0, 14.0])
    with pytest.raises(ProviderParseError, match="NaN"):
        _PLUGIN.validate_frame(df)


def test_short_frame_below_spike_window_is_not_rejected():
    df = _frame([10.0, 200.0])  # too short for the shift(1)/shift(-1) spike check
    _PLUGIN.validate_frame(df)  # must not raise — no false positive on short frames
