from __future__ import annotations

import pandas as pd

from ..indicators import rsi


def rsi_mean_reversion_signals(
    prices: pd.DataFrame,
    period: int = 14,
    low: float = 30.0,
    high: float = 70.0,
) -> pd.Series:
    """Buy when RSI(period) climbs back above `low` after being oversold;
    sell when it drops back under `high` after being overbought."""
    r = rsi(prices["close"], period=period)
    oversold = r < low
    overbought = r > high

    buy = (~oversold) & oversold.shift(1, fill_value=False)
    sell = (~overbought) & overbought.shift(1, fill_value=False)

    signals = pd.Series(0, index=prices.index, dtype=int)
    signals[buy] = 1
    signals[sell] = -1
    return signals
