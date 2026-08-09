"""Regression coverage for the 2026-08-03 SPY regime-veto incident.

A NaN close on the regime symbol (SPY) made `close > sma` evaluate False
forever — `NaN > x` is always False in Python — so the regime gate silently
read "bearish" market-wide for 9 days with zero errors anywhere, in TWO
independent implementations: `IchimokuEquityStrategy._regime_ok` and
`IntradayFlatStrategy._evaluate_regime`. Both now explicitly check for NaN
and fail OPEN with a distinguishable reason instead of a silent False read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy
from tradepro_strategies.paper.strategies.intraday_flat import IntradayFlatStrategy


@pytest.fixture(autouse=True)
def mock_log_run():
    """The NaN/missing-data path fires a best-effort log_run POST — autouse
    so every test gets it mocked by default, not just the one that asserts
    on call count. See the 2026-08-08 incident note in tests/conftest.py:
    an earlier version of this file only mocked it in one test and leaked
    synthetic SPY/NaN data into the live cockpit run log."""
    with patch("tradepro_strategies.run_log.log_run") as mock:
        yield mock


def _spy_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame(
        {"High": [c * 1.01 for c in closes], "Low": [c * 0.99 for c in closes], "Close": closes},
        index=idx,
    )


def _green_regime_closes(n: int = 220) -> list[float]:
    # Monotone uptrend -> last close well above its trailing 200-SMA.
    return list(np.linspace(100.0, 250.0, n))


def _red_regime_closes(n: int = 220) -> list[float]:
    # Monotone downtrend -> last close well below its trailing 200-SMA.
    return list(np.linspace(250.0, 100.0, n))


# ── IchimokuEquityStrategy._regime_ok ───────────────────────────────────
def _ich_strategy(spy_df) -> IchimokuEquityStrategy:
    strat = IchimokuEquityStrategy(
        strategy_id="test-ich-regime",
        params={"symbols": [], "_data_fn": lambda s: spy_df, "regime_symbol": "SPY"},
    )
    strat.on_session_start(datetime.now(timezone.utc) - timedelta(days=30))
    return strat


def test_ich_regime_ok_true_on_real_green_market():
    strat = _ich_strategy(_spy_df(_green_regime_closes()))
    ok, reason = strat._regime_ok(strat._p())
    assert ok is True and reason == "ok"


def test_ich_regime_ok_false_on_real_red_market():
    strat = _ich_strategy(_spy_df(_red_regime_closes()))
    ok, reason = strat._regime_ok(strat._p())
    assert ok is False and reason == "ok"


def test_ich_regime_nan_close_fails_open_not_bearish():
    closes = _green_regime_closes()
    closes[-1] = float("nan")  # the exact 2026-08-03 SPY shape
    strat = _ich_strategy(_spy_df(closes))
    ok, reason = strat._regime_ok(strat._p())
    assert ok is True, "NaN in the regime calc must fail OPEN, not read as bearish"
    assert reason == "data_nan"


def test_ich_regime_missing_data_fails_open_with_reason():
    strat = _ich_strategy(None)
    ok, reason = strat._regime_ok(strat._p())
    assert ok is True and reason == "data_missing"


def test_ich_regime_nan_issue_is_logged_once_per_session(mock_log_run):
    closes = _green_regime_closes()
    closes[-1] = float("nan")
    strat = _ich_strategy(_spy_df(closes))
    strat._regime_ok(strat._p())
    strat._regime_ok(strat._p())
    strat._regime_ok(strat._p())
    assert mock_log_run.call_count == 1, "must log once per session, not once per call"

    # New session resets the guard.
    strat.on_session_start(datetime.now(timezone.utc) - timedelta(days=30))
    strat._regime_ok(strat._p())
    assert mock_log_run.call_count == 2


# ── IntradayFlatStrategy._evaluate_regime ───────────────────────────────
def _flat_strategy(spy_df) -> IntradayFlatStrategy:
    return IntradayFlatStrategy(
        strategy_id="test-flat-regime",
        params={"candidates": ["AAPL"], "_data_fn": lambda s: spy_df,
                "use_regime_filter": True, "regime_symbol": "SPY"},
    )


def test_flat_regime_true_on_real_green_market():
    strat = _flat_strategy(_spy_df(_green_regime_closes()))
    is_bull, detail = strat._evaluate_regime(strat._p())
    assert is_bull is True
    assert detail.get("is_bull") is True


def test_flat_regime_false_on_real_red_market():
    strat = _flat_strategy(_spy_df(_red_regime_closes()))
    is_bull, detail = strat._evaluate_regime(strat._p())
    assert is_bull is False


def test_flat_regime_nan_close_fails_open_not_bearish():
    closes = _green_regime_closes()
    closes[-1] = float("nan")
    strat = _flat_strategy(_spy_df(closes))
    is_bull, detail = strat._evaluate_regime(strat._p())
    assert is_bull is True, "NaN in the regime calc must default BULL, not read as bearish"
    assert "NaN" in detail.get("reason", "")


def test_flat_regime_disabled_short_circuits_before_any_data_read():
    strat = _flat_strategy(None)
    strat.params["use_regime_filter"] = False
    is_bull, detail = strat._evaluate_regime(strat._p())
    assert is_bull is True and "disabled" in detail["reason"]
