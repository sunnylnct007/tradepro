"""SPY 200-SMA bull/bear regime gate.

When SPY is above its 200-day SMA we are in a bull regime (risk-on).
When SPY is below we are in a bear regime (risk-off). The gate zeros
out signals on bear-regime days so the sleeve only holds positions in
confirmed uptrends.

This is a pure price-based macro filter — no fundamentals, no
sentiment. The 200-SMA is a broadly agreed institutional reference
level (many large allocators use it as a regime gate).
"""
from __future__ import annotations

import pandas as pd

from ..indicators import sma


class RegimeFilter:
    """Bull/bear gate based on SPY 200-SMA.

    Parameters
    ----------
    spy_close : pd.Series
        SPY daily adjusted closes, DatetimeIndex.
    sma_period : int
        Rolling window for the simple moving average. Default 200.
    """

    def __init__(self, spy_close: pd.Series, sma_period: int = 200) -> None:
        self.bull_mask = (spy_close > sma(spy_close, sma_period)).fillna(False)

    def is_bull(self, date) -> bool:
        """Return True when `date` falls in a bull-regime bar."""
        return bool(self.bull_mask.get(date, False))

    def align(self, index: pd.Index) -> pd.Series:
        """Reindex the bull mask to an arbitrary DatetimeIndex.

        Missing dates (e.g. when SPY data doesn't cover the full range)
        default to False (bear) so the filter is conservative.
        """
        return self.bull_mask.reindex(index).fillna(False)

    def gate_signals(self, signals: pd.DataFrame) -> pd.DataFrame:
        """Zero-out signal weights on bear-regime days.

        Parameters
        ----------
        signals : pd.DataFrame
            Per-ticker signal weights, DatetimeIndex rows × ticker columns.

        Returns
        -------
        pd.DataFrame
            Same shape as input; rows where regime is bear are zeroed.
        """
        bull = self.align(signals.index).astype(float)
        return signals.mul(bull, axis=0)
