"""Regression coverage for `cache._drop_garbage_bars` — the 2026-08-03 SPY
incident: a NaN-close bar (partial Yahoo fetch) got cached unchecked and made
every downstream `close > x` comparison silently evaluate False forever (NaN
comparisons are always False in Python), reading as "market bearish" instead
of "data broken". `_drop_garbage_bars` previously only caught 4x/0.25x isolated
price-spike bars, not NaN closes — this pins the fix so it can't regress.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tradepro_strategies.cache import _drop_garbage_bars


@pytest.fixture(autouse=True)
def mock_log_run():
    """_drop_garbage_bars fires a best-effort log_run POST whenever it drops
    a row — autouse so every test in this file gets it mocked by default
    (not just the two that assert on it). See the 2026-08-08 incident note
    in tests/conftest.py: an earlier version of this file only mocked it in
    one test and leaked synthetic "TEST"/2026-01-0x data into the live
    cockpit run log."""
    with patch("tradepro_strategies.run_log.log_run") as mock:
        yield mock


def _bars(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes,
         "volume": [1000] * len(closes)},
        index=idx,
    )


def test_nan_close_row_is_dropped():
    df = _bars([10.0, 11.0, np.nan, 13.0, 14.0])
    out = _drop_garbage_bars(df)
    assert len(out) == 4
    assert not out["close"].isna().any()


def test_nan_close_does_not_silently_poison_a_regime_style_comparison():
    """The actual failure mode: `close.iloc[-1] > sma` reading False forever
    when the trailing close is NaN. After the drop, the LAST row must be a
    real number, not NaN."""
    df = _bars([10.0, 11.0, 12.0, 13.0, np.nan])
    out = _drop_garbage_bars(df)
    assert not pd.isna(out["close"].iloc[-1])


def test_isolated_spike_bar_still_dropped_unregressed():
    # Existing holiday-phantom-spike guard must keep working alongside the new check.
    df = _bars([10.0, 11.0, 200.0, 13.0, 14.0])
    out = _drop_garbage_bars(df)
    assert len(out) == 4
    assert 200.0 not in out["close"].values


def test_genuine_rally_is_not_treated_as_a_spike():
    # A real, sustained move (not isolated) must survive.
    df = _bars([10.0, 11.0, 40.0, 41.0, 42.0])
    out = _drop_garbage_bars(df)
    assert len(out) == 5


def test_clean_frame_is_unchanged():
    df = _bars([10.0, 11.0, 12.0, 13.0, 14.0])
    out = _drop_garbage_bars(df)
    assert len(out) == 5


def test_too_few_rows_for_spike_check_still_checks_nan():
    # len(df) < 3 skips the spike-shift logic entirely but must still catch NaN.
    df = _bars([10.0, np.nan])
    out = _drop_garbage_bars(df)
    assert len(out) == 1
    assert not out["close"].isna().any()


def test_drop_is_not_silent_any_more(mock_log_run):
    """The fix itself was silent (dropped rows, told nobody) until this pass —
    pin that it now logs to the central run_log when it fires."""
    df = _bars([10.0, 11.0, np.nan, 13.0, 14.0])
    _drop_garbage_bars(df, symbol="SPY", provider="yahoo")
    mock_log_run.assert_called_once()
    args, kwargs = mock_log_run.call_args
    assert args[0] == "bar-cache"
    assert args[2] == "warn"
    assert kwargs["symbol"] == "SPY"
    assert "NaN" in kwargs["error"]


def test_no_garbage_no_log_call(mock_log_run):
    df = _bars([10.0, 11.0, 12.0])
    _drop_garbage_bars(df, symbol="AAPL", provider="yahoo")
    mock_log_run.assert_not_called()
