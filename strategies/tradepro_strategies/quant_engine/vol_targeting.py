"""Hurst-Ooi-Pedersen (2017) volatility targeting.

The core idea: scale position size up when recent volatility is low and
down when high, so the portfolio targets a constant realised vol. The
scalar is lagged by one bar (shift(1)) so there is no look-ahead bias —
you never know today's vol until the bar closes, so the trade is sized
using yesterday's estimate.

Reference: Hurst, B., Ooi, Y. H., & Pedersen, L. H. (2017). "A Century
of Evidence on Trend-Following Investing." Journal of Portfolio Management.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def vol_target_scalar(
    returns: pd.Series,
    target_vol: float = 0.12,
    max_leverage: float = 1.5,
    lookback: int = 60,
    periods_per_year: int = 252,
) -> pd.Series:
    """Compute the vol-targeting scalar for each bar.

    Scalar = target_vol / realised_vol_lagged, capped at max_leverage.
    The shift(1) ensures the scalar at time t uses only data up to t-1,
    eliminating look-ahead bias. NaN values (before the first full
    lookback window) are filled with 1.0 (no scaling).

    Returns a pd.Series aligned to `returns.index`.
    """
    realised = returns.rolling(lookback).std() * np.sqrt(periods_per_year)
    raw = target_vol / realised.shift(1)
    return raw.clip(upper=max_leverage).fillna(1.0)


def apply_vol_target(
    returns: pd.Series,
    target_vol: float = 0.12,
    max_leverage: float = 1.5,
    lookback: int = 60,
    periods_per_year: int = 252,
) -> tuple[pd.Series, pd.Series]:
    """Apply vol targeting and return (scaled_returns, scalar_series).

    The scaled returns have approximately `target_vol` annualised
    volatility in steady state. After the lookback warm-up period the
    scalar adapts bar-by-bar.

    Returns:
        scaled_returns: returns * scalar (same index as input)
        scalar: the per-bar scaling factor (useful for diagnostics)
    """
    scalar = vol_target_scalar(returns, target_vol, max_leverage, lookback, periods_per_year)
    return returns * scalar, scalar
