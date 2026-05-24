"""Intraday G10 FX Ichimoku mean-reversion strategy.

"Fade the break" — when the Ichimoku signal fires a breakout, take the
opposite (mean-reversion) trade. This exploits the high-frequency
tendency for intraday FX breakouts to fail in liquid G10 pairs.

The strategy is backtested on hourly bars. Positions are capped at
POS_CAP units. Vol-scaled returns are computed per pair and then
averaged into a portfolio.

Reference: Adapted from trader's reference strategy ("main 3.py").
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .portfolio_metrics import summarise as _summarise


# G10 FX pairs and their Yahoo Finance ticker symbols
G10_PAIRS: dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "CHF=X",
    "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

# Ichimoku horizons in hours (Tenkan candidates)
HORIZONS: tuple[int, ...] = (336, 384, 432, 480, 528, 576, 600, 624)

# Smoothing windows for the reversion signal
SMOOTHS: tuple[int, ...] = (24, 48, 72)

# Maximum absolute position in units
POS_CAP: int = 3

# Approximate trading bars per year (24h × 252 trading days)
BARS_PER_YEAR: int = 24 * 252


@dataclass
class FXBacktestResult:
    """Output from FXMeanReversionStrategy.run()."""
    pair_pnls: dict[str, pd.Series]       # pair → cumulative PnL series
    portfolio_pnl: pd.Series              # mean across pairs
    per_pair_stats: dict[str, dict]       # pair → summary dict
    portfolio_stats: dict                 # summary for portfolio_pnl


class FXMeanReversionStrategy:
    """Intraday G10 FX Ichimoku mean-reversion backtester.

    Parameters
    ----------
    horizon : int
        Tenkan window in hours. Default middle of HORIZONS range.
    smooth : int
        Smoothing window for the reversion signal. Default SMOOTHS[1].
    pos_cap : int
        Maximum absolute position. Default POS_CAP.
    """

    def __init__(
        self,
        horizon: int = 480,
        smooth: int = 48,
        pos_cap: int = POS_CAP,
    ) -> None:
        self.horizon = horizon
        self.smooth = smooth
        self.pos_cap = pos_cap

    def _ichimoku_fx(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute Ichimoku lines for hourly FX data.

        Uses horizon as tenkan, 2*horizon as kijun, 4*horizon as senkou_b.
        Senkou spans are not shifted (raw midranges only) for the
        reversion signal — we use the cloud position, not the future cloud.
        """
        h = self.horizon
        k = 2 * h
        sb = 4 * h

        def midrange(window: int) -> pd.Series:
            return (df["High"].rolling(window, min_periods=window).max()
                    + df["Low"].rolling(window, min_periods=window).min()) / 2

        tenkan = midrange(h)
        kijun = midrange(k)
        senkou_a = (tenkan + kijun) / 2
        senkou_b = midrange(sb)

        cloud_high = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
        cloud_low = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

        return pd.DataFrame({
            "tenkan": tenkan,
            "kijun": kijun,
            "cloud_high": cloud_high,
            "cloud_low": cloud_low,
        })

    def _reversion_signal(self, df: pd.DataFrame, ich: pd.DataFrame) -> pd.Series:
        """Mean-reversion signal: fade the Ichimoku breakout.

        +1 when price is above cloud (would normally be bullish) →
           fade by going SHORT (reversion back down expected).
        -1 when price is below cloud → go LONG (reversion up expected).
        0 when price is inside the cloud (no clear break).
        """
        close = df["Close"]
        above = (close > ich["cloud_high"]).astype(float)
        below = (close < ich["cloud_low"]).astype(float)
        # Raw fade signal: -1 when above (short the breakout), +1 when below
        raw_signal = below - above
        # Smooth to reduce noise
        smoothed = raw_signal.rolling(self.smooth, min_periods=1).mean()
        # Discretise: if smoothed > 0 → +1, < 0 → -1, else 0
        signal = pd.Series(0.0, index=df.index)
        signal[smoothed > 0.1] = 1.0
        signal[smoothed < -0.1] = -1.0
        return signal

    def _vol_scale(self, returns: pd.Series, lookback: int = 120) -> pd.Series:
        """Simple hourly vol scaler (target = 10% annual vol)."""
        target = 0.10 / np.sqrt(BARS_PER_YEAR)
        realised = returns.rolling(lookback).std().shift(1).fillna(returns.std())
        realised = realised.replace(0, np.nan).ffill().fillna(1e-6)
        scalar = (target / realised).clip(upper=3.0)
        return scalar

    def _backtest_pair(self, df: pd.DataFrame) -> pd.Series:
        """Run the strategy on a single pair and return PnL series."""
        if len(df) < self.horizon * 4 + self.smooth + 10:
            return pd.Series(0.0, index=df.index, name="pnl")

        ich = self._ichimoku_fx(df)
        signal = self._reversion_signal(df, ich)

        # Position: integral of signal, capped
        position = signal.cumsum().clip(-self.pos_cap, self.pos_cap)
        # Lag position: enter at NEXT bar's open (simplification: use Close)
        position_lag = position.shift(1).fillna(0.0)

        # Bar returns (close-to-close)
        bar_ret = df["Close"].pct_change().fillna(0.0)

        # Vol scale
        scalar = self._vol_scale(bar_ret)

        pnl = position_lag * bar_ret * scalar
        return pnl

    def _perf_stats(self, pnl_series: pd.Series) -> dict:
        """Compute performance stats for a PnL series."""
        equity = (1.0 + pnl_series.fillna(0.0)).cumprod() * 10_000.0
        return _summarise(equity, pnl_series.fillna(0.0), periods_per_year=BARS_PER_YEAR)

    def run(self, pair_data: dict[str, pd.DataFrame]) -> FXBacktestResult:
        """Run the strategy on all pairs.

        Parameters
        ----------
        pair_data : dict[str, pd.DataFrame]
            pair_name → hourly OHLC DataFrame with columns High, Low, Close
            and a DatetimeIndex.

        Returns
        -------
        FXBacktestResult
        """
        pair_pnls: dict[str, pd.Series] = {}
        per_pair_stats: dict[str, dict] = {}

        for pair, df in pair_data.items():
            pnl = self._backtest_pair(df)
            pair_pnls[pair] = pnl
            per_pair_stats[pair] = self._perf_stats(pnl)

        if not pair_pnls:
            empty = pd.Series(dtype=float)
            return FXBacktestResult(
                pair_pnls={},
                portfolio_pnl=empty,
                per_pair_stats={},
                portfolio_stats={},
            )

        # Portfolio PnL = mean across pairs (aligned by index)
        pnl_df = pd.DataFrame(pair_pnls).fillna(0.0)
        portfolio_pnl = pnl_df.mean(axis=1)
        portfolio_stats = self._perf_stats(portfolio_pnl)

        return FXBacktestResult(
            pair_pnls=pair_pnls,
            portfolio_pnl=portfolio_pnl,
            per_pair_stats=per_pair_stats,
            portfolio_stats=portfolio_stats,
        )
