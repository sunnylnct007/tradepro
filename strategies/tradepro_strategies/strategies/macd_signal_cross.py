from __future__ import annotations

import pandas as pd

from ..indicators import macd as macd_indicator


def macd_signal_cross_signals(
    prices: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.Series:
    """Buy when MACD line crosses above its signal line; sell on the reverse."""
    m = macd_indicator(prices["close"], fast=fast, slow=slow, signal=signal)
    above = m["macd"] > m["signal"]
    cross_up = above & ~above.shift(1, fill_value=False)
    cross_dn = ~above & above.shift(1, fill_value=False)
    out = pd.Series(0, index=prices.index, dtype=int)
    out[cross_up] = 1
    out[cross_dn] = -1
    return out
