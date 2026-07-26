"""entry_quality_gate wired into ichimoku_equity's ACTUAL entry (on_bar).

Proves the bot (not just the compare verdict) skips a low-quality long: a
relative-strength laggard or a thin-volume entry (the ANET case). Default OFF is
covered by test_equity_risk_controls.py's parity suite; here we exercise ON.
"""
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from tradepro_strategies.paper.strategies.ichimoku_equity import IchimokuEquityStrategy
from tradepro_strategies.paper.strategy import Bar


def _stale_ts():
    return datetime.now(timezone.utc) - timedelta(days=30)


def _uptrend_df(n=300, level=100.0, last_vol_ratio=1.0):
    """Sustained uptrend → a long Ichimoku signal, with a Volume column whose
    LAST bar is `last_vol_ratio`× the prior-20 average."""
    close = np.linspace(level, level * 2.5, n)
    high = close * 1.01
    low = close * 0.99
    vol = np.full(n, 1_000_000.0)
    vol[-1] = 1_000_000.0 * last_vol_ratio
    return pd.DataFrame({"High": high, "Low": low, "Close": close, "Volume": vol})


def _uptrend_df_no_volume(n=300, level=100.0):
    close = np.linspace(level, level * 2.5, n)
    return pd.DataFrame({"High": close * 1.01, "Low": close * 0.99, "Close": close})


def _bar(symbol, close):
    return Bar(symbol=symbol, timestamp=_stale_ts(), open=close, high=close * 1.01,
              low=close * 0.99, close=close, volume=1_000_000, timeframe_seconds=86400,
              is_live=False)


def _strategy(data_map, **params):
    def data_fn(sym):
        return data_map.get(sym)
    p = {
        "symbols": list(data_map), "use_regime_filter": False, "_data_fn": data_fn,
        "capital_usd": 100_000.0, "sleeve_size": 20, "entry_fresh_only": False,
        **params,
    }
    s = IchimokuEquityStrategy(strategy_id="test-eq-quality", params=p)
    s.on_session_start(_stale_ts())
    return s


def _patch_rs(monkeypatch, score):
    import tradepro_strategies.sector_rs as srs
    monkeypatch.setattr(srs, "compute_sector_rs", lambda s: {"rs_score": score})


def test_gate_off_enters_normally(monkeypatch):
    _patch_rs(monkeypatch, 2)                       # weak RS present but gate off
    s = _strategy({"AAPL": _uptrend_df(last_vol_ratio=0.45)}, entry_quality_gate=False)
    assert len(s.on_bar(_bar("AAPL", 250.0))) == 1  # OFF → enters (parity)


def test_thin_volume_vetoes(monkeypatch):
    _patch_rs(monkeypatch, 9)                        # strong RS, so only volume can veto
    s = _strategy({"AAPL": _uptrend_df(last_vol_ratio=0.45)},
                  entry_quality_gate=True, entry_min_rs=5, entry_min_volume_ratio=0.8)
    assert s.on_bar(_bar("AAPL", 250.0)) == []       # thin 0.45× volume → veto


def test_weak_rs_vetoes(monkeypatch):
    _patch_rs(monkeypatch, 2)                        # laggard RS
    s = _strategy({"AAPL": _uptrend_df(last_vol_ratio=1.5)},  # healthy volume
                  entry_quality_gate=True, entry_min_rs=5, entry_min_volume_ratio=0.8)
    assert s.on_bar(_bar("AAPL", 250.0)) == []       # rs 2 < 5 → veto


def test_strong_rs_and_volume_enters(monkeypatch):
    _patch_rs(monkeypatch, 8)
    s = _strategy({"AAPL": _uptrend_df(last_vol_ratio=1.4)},
                  entry_quality_gate=True, entry_min_rs=5, entry_min_volume_ratio=0.8)
    assert len(s.on_bar(_bar("AAPL", 250.0))) == 1   # both floors cleared → enters


def test_missing_inputs_fail_open(monkeypatch):
    # No volume column + RS fetch returns nothing → both inputs missing. FAIL-OPEN:
    # a data gap must NOT halt a real trade (only a confirmed breach vetoes).
    import tradepro_strategies.sector_rs as srs
    monkeypatch.setattr(srs, "compute_sector_rs", lambda s: {})
    s = _strategy({"AAPL": _uptrend_df_no_volume()},
                  entry_quality_gate=True, entry_min_rs=5, entry_min_volume_ratio=0.8)
    assert len(s.on_bar(_bar("AAPL", 250.0))) == 1   # missing → enters, not blocked
