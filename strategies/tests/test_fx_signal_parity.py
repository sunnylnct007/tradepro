"""Parity guard: the live FX signal must equal the trader's spec, byte-for-byte.

The live `ichimoku_fx_mr` strategy gets its signal from
`tradepro_strategies.paper.strategies._fx_trader_signal`. That module is meant
to be a VERBATIM port of the trader's `docs/main 3.py`
(ichimoku · reversion_signal · vol_scale).

This test embeds an INDEPENDENT copy of the trader's functions (copied from
docs/main 3.py) and asserts the module reproduces them exactly on identical
bars. If anyone edits the module and it drifts from the trader's logic — the
exact failure that produced the HORIZONS-as-lookback / missing-chikou /
state-instead-of-trigger divergence — this test fails.

If the trader revises the spec: update docs/main 3.py + _fx_trader_signal.py +
the golden copy below together, deliberately.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ── GOLDEN reference: verbatim from docs/main 3.py (trader's spec) ────
_HORIZONS = (336, 384, 432, 480, 528, 576, 600, 624)
_SMOOTHS = (24, 48, 72)
_POS_CAP = 3
_BARS_PER_YEAR = 24 * 252
_VOL_TARGET = 0.10


def _trader_ichimoku(df, tenkan_p=9, kijun_p=26, senkou_b_p=52, shift=26):
    h, l, c = df["High"], df["Low"], df["Close"]
    tenkan = (h.rolling(tenkan_p).max() + l.rolling(tenkan_p).min()) / 2
    kijun = (h.rolling(kijun_p).max() + l.rolling(kijun_p).min()) / 2
    ssa = ((tenkan + kijun) / 2).shift(shift)
    ssb = ((h.rolling(senkou_b_p).max() + l.rolling(senkou_b_p).min()) / 2).shift(shift)
    return pd.DataFrame({
        "tenkan": tenkan, "kijun": kijun,
        "cloud_top": np.maximum(ssa, ssb),
        "cloud_bottom": np.minimum(ssa, ssb),
        "chikou_above": c > c.shift(shift),
        "chikou_below": c < c.shift(shift),
    }, index=df.index)


def _trader_reversion_signal(df, ich):
    close = df["Close"].values
    ten, kij = ich["tenkan"].values, ich["kijun"].values
    cbot, ctop = ich["cloud_bottom"].values, ich["cloud_top"].values
    chi_a, chi_b = ich["chikou_above"].values, ich["chikou_below"].values
    bullish_break = (close > ctop) & (ten > kij) & chi_a
    bearish_break = (close < cbot) & (ten < kij) & chi_b
    short_trig = bullish_break & ~np.roll(bullish_break, 1); short_trig[0] = False
    long_trig = bearish_break & ~np.roll(bearish_break, 1); long_trig[0] = False
    n = len(df)
    raw = np.zeros(n)
    for hh in _HORIZONS:
        s = np.zeros(n)
        for j in np.where(long_trig)[0]:  s[j:min(j + hh, n)] += 1
        for j in np.where(short_trig)[0]: s[j:min(j + hh, n)] -= 1
        raw += np.clip(s, -_POS_CAP, _POS_CAP)
    raw = pd.Series(raw / len(_HORIZONS), index=df.index)
    return sum(raw.rolling(s, min_periods=1).mean() for s in _SMOOTHS) / len(_SMOOTHS)


def _trader_vol_scale(signal, close, target=_VOL_TARGET, lookback=24 * 20):
    rv = (np.log(close).diff().rolling(lookback).std() * np.sqrt(_BARS_PER_YEAR)
          ).shift(1).clip(lower=0.01)
    return (signal * (target / rv)).clip(-_POS_CAP, _POS_CAP)


def _golden_position_series(df):
    ich = _trader_ichimoku(df)
    sig = _trader_reversion_signal(df, ich)
    return _trader_vol_scale(sig, df["Close"])


# ── synthetic bars (deterministic) ───────────────────────────────────
def _make_bars(seed: int, n: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed)
    steps = rng.normal(0, 0.0012, n)
    close = 1.10 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.0006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.0006, n)))
    return pd.DataFrame({"High": high, "Low": low, "Close": close})


@pytest.mark.parametrize("seed", [1, 7, 42, 2024])
def test_module_matches_trader_spec_full_series(seed):
    """The module's vol-scaled position series == the trader's, element-wise."""
    from tradepro_strategies.paper.strategies import _fx_trader_signal as mod
    df = _make_bars(seed, mod.MIN_BARS + 120)
    golden = _golden_position_series(df).to_numpy()
    actual = mod.trader_position_series(
        df["Close"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()
    ).to_numpy()
    # equal_nan: warmup region is NaN in both; assert the rest matches exactly.
    assert np.allclose(golden, actual, rtol=0, atol=1e-9, equal_nan=True), (
        "live FX signal has DRIFTED from the trader's spec (docs/main 3.py)"
    )


@pytest.mark.parametrize("seed", [1, 7, 42, 2024])
def test_latest_target_position_matches_trader(seed):
    """The single value the live strategy acts on == the trader's last value."""
    from tradepro_strategies.paper.strategies._fx_trader_signal import latest_target_position, MIN_BARS
    df = _make_bars(seed, MIN_BARS + 120)
    golden_last = float(_golden_position_series(df).iloc[-1])
    actual = latest_target_position(
        df["Close"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()
    )
    if np.isnan(golden_last):
        golden_last = 0.0
    assert actual == pytest.approx(golden_last, abs=1e-9)


def test_constants_match_trader_spec():
    """HORIZONS/SMOOTHS/POS_CAP/VOL_TARGET must equal the trader's."""
    from tradepro_strategies.paper.strategies import _fx_trader_signal as mod
    assert mod.HORIZONS == _HORIZONS
    assert mod.SMOOTHS == _SMOOTHS
    assert mod.POS_CAP == _POS_CAP
    assert mod.VOL_TARGET == _VOL_TARGET
