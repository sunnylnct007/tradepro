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


def ichimoku(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Ichimoku Cloud — five lines that together describe trend, momentum,
    and forward support/resistance. Built for the energy-commodities
    sprint where the cloud boundaries double as price targets and the
    Kijun-sen as the natural stop, but the math is universal so the
    strategy registry exposes it for any universe.

    All periods configurable so a per-universe override (energy might
    prefer 7/22/44) can dial them. Displacement (forward shift for
    Senkou spans, backward shift for Chikou) is conventionally fixed
    to kijun but kept separate for future tuning.

    Returns a DataFrame with: tenkan, kijun, senkou_a, senkou_b,
    chikou, cloud_high, cloud_low, cloud_thickness. Senkou spans are
    aligned to the bar they DESCRIBE (i.e. the value at index i is the
    cloud you should compare price_i against — shifted forward at
    construction time so the trader reads "today's cloud" off "today's
    row").
    """
    def _midrange(window: int) -> pd.Series:
        return (high.rolling(window=window, min_periods=window).max()
                + low.rolling(window=window, min_periods=window).min()) / 2

    tenkan_line = _midrange(tenkan)
    kijun_line = _midrange(kijun)
    # Senkou Span A is the midpoint of Tenkan + Kijun, shifted forward.
    # Aligning to the bar-being-described means shift(+displacement).
    senkou_a = ((tenkan_line + kijun_line) / 2).shift(displacement)
    senkou_b = _midrange(senkou_b).shift(displacement)
    # Chikou is today's close shifted BACK, so the value at index i is
    # the close `displacement` bars in the future relative to bar i.
    # Used to verify "current price is higher than where it was X bars
    # ago" without indexing arithmetic in the strategy code.
    chikou = close.shift(-displacement)

    cloud_high = pd.concat([senkou_a, senkou_b], axis=1).max(axis=1)
    cloud_low = pd.concat([senkou_a, senkou_b], axis=1).min(axis=1)

    return pd.DataFrame({
        "tenkan": tenkan_line,
        "kijun": kijun_line,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "chikou": chikou,
        "cloud_high": cloud_high,
        "cloud_low": cloud_low,
        "cloud_thickness": cloud_high - cloud_low,
    })


def bollinger(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands. Middle band is the rolling mean; outer bands
    are ±num_std rolling-stdev wide. Used for mean-reversion entries
    (touch lower band + oversold = candidate BUY) and for volatility
    regime detection (band width contracts before big moves).

    Returns:
      middle:     rolling mean
      upper:      middle + num_std * stdev
      lower:      middle - num_std * stdev
      bandwidth:  (upper - lower) / middle  (volatility regime proxy)
      percent_b:  (price - lower) / (upper - lower)  (0 = at lower,
                  1 = at upper; <0 = below lower, >1 = above upper)
    """
    middle = series.rolling(window=window, min_periods=window).mean()
    stdev = series.rolling(window=window, min_periods=window).std()
    upper = middle + num_std * stdev
    lower = middle - num_std * stdev
    width = upper - lower
    bandwidth = width / middle
    # Guard against divide-by-zero when upper == lower (flat series).
    percent_b = (series - lower) / width.replace(0, pd.NA)
    return pd.DataFrame({
        "middle": middle,
        "upper": upper,
        "lower": lower,
        "bandwidth": bandwidth,
        "percent_b": percent_b,
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
