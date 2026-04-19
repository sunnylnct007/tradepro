import pandas as pd

from ..indicators import sma


def sma_crossover_signals(prices: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.Series:
    fast_line = sma(prices["close"], fast)
    slow_line = sma(prices["close"], slow)
    above = fast_line > slow_line
    cross_up = above & ~above.shift(1, fill_value=False)
    cross_dn = ~above & above.shift(1, fill_value=False)
    signals = pd.Series(0, index=prices.index, dtype=int)
    signals[cross_up] = 1
    signals[cross_dn] = -1
    return signals
