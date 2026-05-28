"""Bollinger Band mean-reversion strategy.

Long entry: close crosses below the lower band AND RSI confirms
oversold (default RSI < 35). The dual-trigger filters out the
"pricing in a new lower regime" false positive where price just
walks down the lower band — RSI staying near 50 in those moves
keeps us flat.

Long exit: close reaches the middle band (mean reversion target
realised), OR close crosses above the upper band (overshoot — take
profit before the inevitable retrace).

Returns the standard signed signal series. Pairs naturally with the
existing rsi_mean_reversion strategy — Bollinger gives the geometric
"how far from average" the RSI threshold can't capture by itself."""
from __future__ import annotations

import pandas as pd

from ..indicators import bollinger as bollinger_indicator
from ..indicators import rsi as rsi_indicator


def bollinger_bounce_signals(
    prices: pd.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
    rsi_period: int = 14,
    rsi_oversold: float = 35.0,
) -> pd.Series:
    """+1 = long entry (price below lower band AND RSI oversold).
    -1 = long exit (price reaches middle OR upper band).
     0 = no action."""
    close = prices["close"]
    bb = bollinger_indicator(close, window=window, num_std=num_std)
    rsi_v = rsi_indicator(close, rsi_period)

    below_lower = close < bb["lower"]
    oversold = rsi_v < rsi_oversold
    # Entry fires on the FIRST bar both conditions are true — staying
    # below the band for multiple bars only triggers once, otherwise
    # the simulator would re-buy every bar of a sustained breakdown.
    entry_condition = below_lower & oversold
    entry_prev = entry_condition.shift(1, fill_value=False)
    entry = entry_condition & ~entry_prev

    above_middle = close >= bb["middle"]
    above_upper = close > bb["upper"]
    exit_condition = above_middle | above_upper
    exit_prev = exit_condition.shift(1, fill_value=False)
    exit_ = exit_condition & ~exit_prev

    out = pd.Series(0, index=prices.index, dtype=int)
    out[entry] = 1
    out[exit_] = -1
    return out
