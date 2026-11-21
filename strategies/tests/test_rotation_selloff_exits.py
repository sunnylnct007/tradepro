"""V4 sell-off exits (MOMENTUM_ROTATION_GATES_V4_SELLOFF_EXITS.md)."""
from types import SimpleNamespace

import pandas as pd
import pytest

from tradepro_strategies.momentum_rotation import (
    Params, _market_exit_series, _week_ends, with_overrides)


def _panels(prices):
    idx = pd.bdate_range("2025-01-01", periods=len(prices))
    return SimpleNamespace(spy=pd.Series(prices, index=idx, dtype=float), dates=idx)


def test_default_is_the_decided_design():
    assert Params().market_exit is None
    assert _market_exit_series(_panels([100.0] * 300), Params()) is None


def test_spy200_fires_only_below_the_average():
    p = _panels([100.0] * 250 + [90.0] * 5)
    s = _market_exit_series(p, with_overrides(market_exit="spy200_daily"))
    assert not s.iloc[249] and s.iloc[-1]


def test_the_buffer_waits_for_three_percent():
    p = _panels([100.0] * 250 + [98.0])          # 2% below: not yet
    assert not _market_exit_series(p, with_overrides(market_exit="spy200_buffer3")).iloc[-1]
    p = _panels([100.0] * 250 + [96.0])
    assert _market_exit_series(p, with_overrides(market_exit="spy200_buffer3")).iloc[-1]


def test_an_unknown_exit_is_refused():
    with pytest.raises(ValueError):
        _market_exit_series(_panels([100.0] * 10), with_overrides(market_exit="spy7"))


def test_week_ends_are_fridays_or_the_last_session():
    idx = pd.bdate_range("2026-11-02", "2026-11-20")
    assert [d.strftime("%a") for d in _week_ends(idx)] == ["Fri", "Fri", "Fri"]
