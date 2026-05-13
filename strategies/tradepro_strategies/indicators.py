"""Vectorised indicator functions. Heavy loops belong in numpy / pandas, not
Python — keep it that way so we can run big universes locally on the M4."""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "hist": macd_line - signal_line,
    })


def atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Wilder's Average True Range. Returns a series of absolute-price
    volatility values — useful for sizing positions ("don't put more
    capital at risk than X% per trade") and setting volatility-aware
    stops ("trailing stop = 2x ATR" instead of a fixed %).

    True Range for bar i is the max of:
      - high_i - low_i                     (today's intraday range)
      - |high_i - close_{i-1}|             (gap up)
      - |low_i  - close_{i-1}|             (gap down)

    Wilder's smoothing is an EMA with alpha = 1/period. First period-1
    bars are NaN because there's no prior close to gap-test against.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
