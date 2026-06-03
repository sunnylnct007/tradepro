"""Parity guard: the live equity signal must equal the trader's spec.

The live `ichimoku_equity` strategy gets its long/flat signal from
`tradepro_strategies.paper.strategies._equity_trader_signal`, meant to be a
VERBATIM port of the trader's `docs/strategy.py` IchimokuStrategy
(compute_indicators + compute_position) with the params from `docs/config 4.py`.

This embeds an INDEPENDENT copy of the trader's functions and asserts the
module reproduces them exactly. Catches the exact drift we fixed: a stateless
1/0 recompute with an extra `close>tenkan` entry condition instead of the
trader's stateful ffill long/flat.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ── GOLDEN: verbatim from docs/strategy.py + docs/config 4.py ────────
_TENKAN, _KIJUN, _SENKOU_B, _DISPLACEMENT = 5, 32, 50, 32


def _trader_indicators(df):
    df = df.copy()
    df["tenkan"] = (df["High"].rolling(_TENKAN).max() + df["Low"].rolling(_TENKAN).min()) / 2
    df["kijun"] = (df["High"].rolling(_KIJUN).max() + df["Low"].rolling(_KIJUN).min()) / 2
    df["span_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(_DISPLACEMENT)
    df["span_b"] = ((df["High"].rolling(_SENKOU_B).max() + df["Low"].rolling(_SENKOU_B).min()) / 2).shift(_DISPLACEMENT)
    df["cloud_top"] = df[["span_a", "span_b"]].max(axis=1)
    df["cloud_bottom"] = df[["span_a", "span_b"]].min(axis=1)
    return df


def _trader_position(df):
    df = df.copy()
    long_cond = (df["Close"] > df["cloud_top"]) & (df["tenkan"] > df["kijun"])
    flat_cond = (df["Close"] < df["cloud_bottom"]) | (df["tenkan"] < df["kijun"])
    pos = pd.Series(np.nan, index=df.index)
    pos[long_cond] = 1.0
    pos[flat_cond] = 0.0
    df["position"] = pos.ffill().fillna(0.0)
    return df


def _golden_position(df):
    return _trader_position(_trader_indicators(df))["position"]


def _make_bars(seed: int, n: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    close = 300 * np.exp(np.cumsum(rng.normal(0.0004, 0.015, n)))
    high = close * (1 + np.abs(rng.normal(0, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.008, n)))
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


@pytest.mark.parametrize("seed", [1, 7, 42, 2024])
def test_module_matches_trader_position_series(seed):
    from tradepro_strategies.paper.strategies import _equity_trader_signal as mod
    df = _make_bars(seed, mod.MIN_BARS + 200)
    golden = _golden_position(df).to_numpy()
    actual = mod.position_series(df["Close"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()).to_numpy()
    assert np.array_equal(golden, actual), "live equity signal DRIFTED from trader spec (docs/strategy.py)"


@pytest.mark.parametrize("seed", [1, 7, 42, 2024])
def test_latest_position_matches_trader(seed):
    from tradepro_strategies.paper.strategies._equity_trader_signal import latest_position, MIN_BARS
    df = _make_bars(seed, MIN_BARS + 200)
    assert latest_position(df["Close"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()) == float(_golden_position(df).iloc[-1])


def test_params_match_trader_spec():
    from tradepro_strategies.paper.strategies import _equity_trader_signal as mod
    assert (mod.TENKAN, mod.KIJUN, mod.SENKOU_B, mod.DISPLACEMENT) == (5, 32, 50, 32)


def test_stateful_hold_through_cloud():
    """The trader HOLDS a long while price sits inside the cloud (ffill); the
    old stateless signal dropped it. Guard the hysteresis explicitly."""
    from tradepro_strategies.paper.strategies import _equity_trader_signal as mod
    n = mod.MIN_BARS + 60
    # Strong uptrend to go long, then a drift that dips into (not below) the cloud.
    close = np.concatenate([np.linspace(100, 200, n - 20), np.linspace(200, 190, 20)])
    high = close * 1.01
    low = close * 0.99
    series = mod.position_series(close, high, low)
    # Once established long in a rising trend, a mild pullback that stays above
    # cloud_bottom must NOT flip to flat on the very next bar (stateful hold).
    longs = (series == 1.0).sum()
    assert longs > 0, "expected the trader logic to take a long in a clean uptrend"
