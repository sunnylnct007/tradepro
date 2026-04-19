import pandas as pd


def buy_and_hold_signals(prices: pd.DataFrame) -> pd.Series:
    signals = pd.Series(0, index=prices.index, dtype=int)
    if not signals.empty:
        signals.iloc[0] = 1
    return signals
