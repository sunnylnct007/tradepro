"""Faithful port of the trader's FX mean-reversion signal.

This is a VERBATIM copy of the signal math the trader provided in
`docs/main 3.py` (ichimoku · reversion_signal · vol_scale). It is the single
source of truth for the live `ichimoku_fx_mr` strategy: the strategy calls
`latest_target_position()` on its rolling window each bar and acts on the last
value, so the live signal cannot drift from the trader's spec.

WHY a verbatim port (not a streaming re-implementation): the previous live
signal was a hand-written streaming reinterpretation that silently drifted —
it reused HORIZONS as an Ichimoku LOOKBACK window (the trader uses them as
holding periods), dropped the chikou condition, and replaced edge-triggered
holds with instantaneous cloud state. A re-implementation is exactly how that
happens, so we keep the trader's functions byte-for-byte and only wrap them.
`tests/.../test_fx_signal_parity.py` pins this module against an independent
copy of the trader's functions so any future edit that drifts fails the build.

Keep the function bodies below identical to `docs/main 3.py`. If the trader
revises the spec, update there + here together and the parity test will guard.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── trader constants (verbatim from docs/main 3.py) ──────────────────
HORIZONS = (336, 384, 432, 480, 528, 576, 600, 624)   # holding periods, 2 → 3.5 weeks
SMOOTHS = (24, 48, 72)                                 # smoothing windows, hours
POS_CAP = 3                                            # max leverage per pair
BARS_PER_YEAR = 24 * 252
VOL_TARGET = 0.10                                      # 10% annualised per pair leg

# Minimum bars before the latest value is representative: the cloud
# (senkou_b 52 + 26 forward shift), the longest holding horizon (624), the
# longest smooth (72), and the vol lookback (480) must all be filled.
MIN_BARS = max(max(HORIZONS), 24 * 20) + 52 + 26 + max(SMOOTHS)  # = 774


def ichimoku(df: pd.DataFrame, tenkan_p: int = 9, kijun_p: int = 26,
             senkou_b_p: int = 52, shift: int = 26) -> pd.DataFrame:
    """Classic Ichimoku — no look-ahead (Senkou A/B forward-shifted by `shift`)."""
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


def reversion_signal(df: pd.DataFrame, ich: pd.DataFrame) -> pd.Series:
    """Fade the cloud break. Stack ±1 unit/trigger across the horizon ensemble, then smooth."""
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
    for hh in HORIZONS:
        s = np.zeros(n)
        for j in np.where(long_trig)[0]:  s[j:min(j + hh, n)] += 1
        for j in np.where(short_trig)[0]: s[j:min(j + hh, n)] -= 1
        raw += np.clip(s, -POS_CAP, POS_CAP)
    raw = pd.Series(raw / len(HORIZONS), index=df.index)
    return sum(raw.rolling(s, min_periods=1).mean() for s in SMOOTHS) / len(SMOOTHS)


def vol_scale(signal: pd.Series, close: pd.Series,
              target: float = VOL_TARGET, lookback: int = 24 * 20) -> pd.Series:
    """Scale signal by inverse realised vol — uses only past info."""
    rv = (np.log(close).diff().rolling(lookback).std() * np.sqrt(BARS_PER_YEAR)
          ).shift(1).clip(lower=0.01)
    return (signal * (target / rv)).clip(-POS_CAP, POS_CAP)


# ── streaming wrapper used by the live strategy ──────────────────────
def trader_position_series(closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray) -> pd.Series:
    """Run the trader's full pipeline over a window and return the
    vol-scaled position series (∈ [-POS_CAP, POS_CAP])."""
    df = pd.DataFrame({"High": np.asarray(highs, float),
                       "Low": np.asarray(lows, float),
                       "Close": np.asarray(closes, float)})
    ich = ichimoku(df)
    sig = reversion_signal(df, ich)
    return vol_scale(sig, df["Close"])


def latest_target_position(closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray) -> float:
    """The latest vol-scaled position the trader's logic would hold.
    Returns 0.0 if there isn't enough history or the value is NaN."""
    if len(closes) < MIN_BARS:
        return 0.0
    pos = trader_position_series(closes, highs, lows)
    v = float(pos.iloc[-1])
    return 0.0 if np.isnan(v) else v
