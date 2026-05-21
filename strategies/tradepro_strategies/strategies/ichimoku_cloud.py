"""Ichimoku Cloud strategy.

Long entry: close crosses above the cloud (max of Senkou A/B) AND the
Chikou span confirms — i.e. price now is higher than price was 26 bars
ago. Combined, that says "we just broke above the forward cloud
projected when the market was lower than today" — a momentum-with-
trend gate that filters out the kind of weak breakouts the simple
sma-crossover happily takes.

Long exit: close crosses below Kijun-sen. Kijun is the 26-bar
midrange — when a true uptrend rolls, that's the line that breaks
first. Treating it as the exit means we ride the trend until that
support fails.

Returns the standard signed signal series. Auxiliary fields
(price_target, stop_level, cloud_position, etc.) are computed by the
companion `ichimoku_targets` helper below — the comparator pulls
those into the row's strategy result so the website can render
"BUY → target X, stop Y".
"""
from __future__ import annotations

import pandas as pd

from ..indicators import ichimoku as ichimoku_indicator


def ichimoku_cloud_signals(
    prices: pd.DataFrame,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> pd.Series:
    """Long-only Ichimoku signal series.

    +1 = long entry (price just broke above the cloud AND Chikou
         confirms higher-than-26-bars-ago)
    -1 = long exit (close crossed below Kijun)
     0 = no action
    """
    if "high" not in prices.columns or "low" not in prices.columns:
        # Strategy needs OHLC; degrade to all-flat rather than throw.
        return pd.Series(0, index=prices.index, dtype=int)

    ich = ichimoku_indicator(
        prices["high"], prices["low"], prices["close"],
        tenkan=tenkan, kijun=kijun, senkou_b=senkou_b,
        displacement=displacement,
    )
    close = prices["close"]

    # Long-entry gate. The "Chikou above price 26 bars ago" rule is
    # equivalent to "close_today > close_{today-26}" because the Chikou
    # at index i is close_{i+displacement} — comparing it to close_i
    # is the same test, just easier to read.
    above_cloud = close > ich["cloud_high"]
    above_cloud_prev = above_cloud.shift(1, fill_value=False)
    cross_above_cloud = above_cloud & ~above_cloud_prev
    chikou_confirms = close > close.shift(displacement)

    # Long-exit gate.
    below_kijun = close < ich["kijun"]
    below_kijun_prev = below_kijun.shift(1, fill_value=False)
    cross_below_kijun = below_kijun & ~below_kijun_prev

    out = pd.Series(0, index=prices.index, dtype=int)
    out[cross_above_cloud & chikou_confirms] = 1
    out[cross_below_kijun] = -1
    return out


def ichimoku_targets(
    prices: pd.DataFrame,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b: int = 52,
    displacement: int = 26,
) -> dict:
    """Compute the auxiliary fields the comparator surfaces alongside
    the signal:

      price_target: Senkou Span B projected forward — the cloud
        boundary opposite the current price; what the trade is
        targeting if the breakout holds.
      stop_level:   Kijun-sen at the latest bar — the invalidation
        level. Below this and the entry thesis is broken.
      rr_ratio:     (price_target - last_close) / (last_close - stop_level)
      cloud_position: ABOVE / INSIDE / BELOW the cloud
      cloud_high / cloud_low / cloud_thickness / tenkan / kijun:
        raw line values so the UI can plot them.

    Returns {} for series too short to produce all the lines (no NaN
    pollution in the row dict — the renderer treats missing keys as
    "not applicable").
    """
    if "high" not in prices.columns or "low" not in prices.columns:
        return {}
    if len(prices) < max(senkou_b, displacement) + 1:
        return {}

    ich = ichimoku_indicator(
        prices["high"], prices["low"], prices["close"],
        tenkan=tenkan, kijun=kijun, senkou_b=senkou_b,
        displacement=displacement,
    )
    last_close = float(prices["close"].iloc[-1])
    # Senkou Span A/B at the last bar IS the cloud the trader is
    # currently looking at (we shifted forward at construction time).
    cloud_high = ich["cloud_high"].iloc[-1]
    cloud_low = ich["cloud_low"].iloc[-1]
    senkou_b_val = ich["senkou_b"].iloc[-1]
    kijun_val = ich["kijun"].iloc[-1]
    tenkan_val = ich["tenkan"].iloc[-1]

    if pd.isna(cloud_high) or pd.isna(cloud_low) or pd.isna(kijun_val):
        return {}

    cloud_position: str
    if last_close > cloud_high:
        cloud_position = "ABOVE"
    elif last_close < cloud_low:
        cloud_position = "BELOW"
    else:
        cloud_position = "INSIDE"

    # Target / stop / R-R are only meaningful when we'd be entering
    # long: above the cloud and with kijun below. Compute regardless
    # but the comparator only surfaces them when the strategy is
    # actually in position.
    #
    # senkou_b serves as the natural overhead resistance for INSIDE /
    # BELOW-cloud setups. For ABOVE-cloud entries the cloud has
    # already been broken and senkou_b sits BELOW the current price
    # as a trail-stop boundary — using it as a "take-profit target"
    # gives a NEGATIVE reward and a meaningless R/R. MTUM 2026-05-20
    # surfaced that as "R/R -4.0×" next to a WAIT verdict. Guard
    # against it by null-ing the target whenever reward would be ≤ 0,
    # so the UI renders "—" instead of a fake number. stop_level
    # still surfaces because the kijun line IS the invalidation
    # reference regardless of whether we'd enter today.
    raw_target = float(senkou_b_val) if not pd.isna(senkou_b_val) else None
    stop_level = float(kijun_val)
    price_target: float | None = None
    rr_ratio: float | None = None
    if (
        raw_target is not None
        and last_close > stop_level
        and raw_target > last_close
    ):
        risk = last_close - stop_level
        reward = raw_target - last_close
        if risk > 0:
            price_target = raw_target
            rr_ratio = round(reward / risk, 2)

    return {
        "price_target": price_target,
        "stop_level": stop_level,
        "rr_ratio": rr_ratio,
        "cloud_position": cloud_position,
        "cloud_high": float(cloud_high),
        "cloud_low": float(cloud_low),
        "cloud_thickness": float(cloud_high - cloud_low),
        "tenkan_sen": float(tenkan_val) if not pd.isna(tenkan_val) else None,
        "kijun_sen": float(kijun_val),
    }
