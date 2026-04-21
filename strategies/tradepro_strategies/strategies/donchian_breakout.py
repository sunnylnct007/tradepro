from __future__ import annotations

import pandas as pd


def donchian_breakout_signals(prices: pd.DataFrame, lookback: int = 20) -> pd.Series:
    """Buy when today's close exceeds the prior N-day high; sell when it
    drops below the prior N-day low."""
    closes = prices["close"]
    # `shift(1)` so today's close is compared against the *prior* window only.
    rolling_high = closes.rolling(lookback).max().shift(1)
    rolling_low = closes.rolling(lookback).min().shift(1)
    above_high = closes > rolling_high
    below_low = closes < rolling_low
    cross_up = above_high & ~above_high.shift(1, fill_value=False)
    cross_dn = below_low & ~below_low.shift(1, fill_value=False)
    out = pd.Series(0, index=prices.index, dtype=int)
    out[cross_up] = 1
    out[cross_dn] = -1
    return out
