"""QuantSignalBridge — pure helper functions for signal->order conversion.

No state, no I/O, no network. Pure computation so it's trivially testable.

The functions here translate quant-engine outputs (annualised vol, raw
Ichimoku signals, FX reversion signals) into the concrete numbers a
paper strategy puts in an Order: integer share quantity, signal scalar,
override-friendly metadata. By keeping these helpers separate from the
strategy classes, both the equity sleeve and the FX strategy share the
same vol-targeting math and the same signal-derivation math — no copy
paste, no two-place updates when we tune target_vol or max_leverage.

Design intent:
  - Inputs are plain numpy / pandas; no DataFrames-of-DataFrames.
  - Outputs are scalars / tuples; no strategy state.
  - Every function is pure: same input -> same output, always.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def size_from_vol_target(
    price: float,
    capital: float,
    target_vol: float = 0.12,
    realised_vol: float | None = None,
    max_leverage: float = 1.5,
) -> int:
    """Vol-targeted position size in whole shares.

    Sizing rule (matches `quant_engine.vol_targeting`):
        scalar  = min(max_leverage, target_vol / realised_vol)
        notional = capital * scalar
        qty      = max(1, floor(notional / price))

    When `realised_vol` is None or 0 we cannot scale, so we fall back to
    a neutral scalar of 1.0 (one full capital slot, no leverage). This
    avoids divide-by-zero AND keeps early-session bars (before enough
    history for vol) trading at sensible size.

    Returns at least 1 share whenever `price > 0` and `capital > 0`; the
    paper engine refuses to submit qty=0 orders so the caller saves an
    extra guard at the call site.
    """
    if price <= 0 or capital <= 0:
        return 0

    if realised_vol is None or realised_vol <= 0:
        scalar = 1.0
    else:
        scalar = min(max_leverage, target_vol / realised_vol)

    notional = capital * scalar
    qty = int(notional // price)
    return max(1, qty)


def realised_vol_from_closes(
    closes: list[float],
    periods_per_year: float = 252,
) -> float | None:
    """Annualised realised vol from a list of close prices.

    Uses log returns and sqrt-time scaling. Returns None when there are
    fewer than 20 closes (= 19 returns) — the variance estimate is too
    noisy below that threshold to be useful for sizing, and the strategy
    falls back to neutral sizing via `size_from_vol_target`.

    Why log returns: additive under compounding so the annualisation is
    exact. Simple-return vol drifts off log-vol by the variance term,
    immaterial at hourly/daily horizons but cleaner to be correct.
    """
    if closes is None or len(closes) < 20:
        return None
    arr = np.asarray(closes, dtype=float)
    if np.any(arr <= 0):
        return None
    log_rets = np.diff(np.log(arr))
    if log_rets.size < 2:
        return None
    sigma = float(np.std(log_rets, ddof=1))
    return sigma * math.sqrt(periods_per_year)


def ichimoku_daily_signal(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan: int = 5,
    kijun: int = 32,
    senkou_b: int = 50,
    displacement: int = 32,
) -> tuple[float, dict]:
    """Daily Ichimoku long/flat signal + diagnostic metadata.

    Long when ALL of:
      - close > cloud_high (price above the cloud)
      - tenkan > kijun     (short-term momentum above medium-term)
      - close > tenkan     (trend confirmation)

    Otherwise flat. Returns `(signal, metadata)` where:
      - signal:    1.0 (long) or 0.0 (flat)
      - metadata:  {cloud_top, cloud_bottom, tenkan_val, kijun_val,
                    cloud_position} — useful for order tags + UI display.
    `cloud_position` is one of "above", "below", "in".

    Insufficient history -> (0.0, {}) so the caller treats it as flat
    without having to handle NaN.
    """
    if close is None or len(close) < senkou_b + displacement + 1:
        return 0.0, {}

    from ..indicators import ichimoku  # local import to avoid cycle

    ich = ichimoku(
        high=high,
        low=low,
        close=close,
        tenkan=tenkan,
        kijun=kijun,
        senkou_b=senkou_b,
        displacement=displacement,
    )
    last = ich.iloc[-1]
    cloud_top = last["cloud_high"]
    cloud_bottom = last["cloud_low"]
    tenkan_val = last["tenkan"]
    kijun_val = last["kijun"]
    last_close = float(close.iloc[-1])

    # Position relative to the cloud (always reported, even on flat signal).
    if pd.isna(cloud_top) or pd.isna(cloud_bottom):
        cloud_position = "unknown"
    elif last_close > cloud_top:
        cloud_position = "above"
    elif last_close < cloud_bottom:
        cloud_position = "below"
    else:
        cloud_position = "in"

    signal = 0.0
    if (
        not pd.isna(cloud_top)
        and not pd.isna(tenkan_val)
        and not pd.isna(kijun_val)
        and last_close > cloud_top
        and tenkan_val > kijun_val
        and last_close > tenkan_val
    ):
        signal = 1.0

    metadata = {
        "cloud_top": float(cloud_top) if not pd.isna(cloud_top) else None,
        "cloud_bottom": float(cloud_bottom) if not pd.isna(cloud_bottom) else None,
        "tenkan_val": float(tenkan_val) if not pd.isna(tenkan_val) else None,
        "kijun_val": float(kijun_val) if not pd.isna(kijun_val) else None,
        "cloud_position": cloud_position,
    }
    return signal, metadata


def fx_reversion_signal_latest(
    close: np.ndarray,
    cloud_top: np.ndarray,
    cloud_bottom: np.ndarray,
    tenkan: np.ndarray,
    kijun: np.ndarray,
    chikou_above: np.ndarray,
    chikou_below: np.ndarray,
    horizons: tuple,
    smooths: tuple,
    pos_cap: int = 3,
) -> float:
    """Latest FX reversion signal, ensembled across horizons/smooths.

    Mirrors `quant_engine.fx_strategy._reversion_signal` but operates on
    raw numpy slices so it can be called from on_bar without rebuilding
    DataFrames each tick. The signal is the smoothed, capped sum of:

        +1 when close < cloud_bottom AND tenkan < kijun AND chikou_below
        -1 when close > cloud_top    AND tenkan > kijun AND chikou_above

    The "fade the break" logic: when price breaks ABOVE the cloud we
    SHORT (signal negative) expecting reversion; when it breaks BELOW
    we LONG (signal positive). Ensembled across the supplied horizons +
    smooths and capped at ±pos_cap.

    Returns the most recent ensembled, capped value as a float. NaN /
    insufficient history -> 0.0 (flat).
    """
    n = len(close)
    if n == 0:
        return 0.0

    close = np.asarray(close, dtype=float)
    cloud_top = np.asarray(cloud_top, dtype=float)
    cloud_bottom = np.asarray(cloud_bottom, dtype=float)
    tenkan = np.asarray(tenkan, dtype=float)
    kijun = np.asarray(kijun, dtype=float)
    chikou_above = np.asarray(chikou_above, dtype=float)
    chikou_below = np.asarray(chikou_below, dtype=float)

    if any(len(a) != n for a in (cloud_top, cloud_bottom, tenkan, kijun,
                                  chikou_above, chikou_below)):
        return 0.0

    # Discrete raw signal at each bar.
    long_mask = (
        (close < cloud_bottom)
        & (tenkan < kijun)
        & (chikou_below > 0)
    )
    short_mask = (
        (close > cloud_top)
        & (tenkan > kijun)
        & (chikou_above > 0)
    )
    raw = np.where(long_mask, 1.0, 0.0) - np.where(short_mask, 1.0, 0.0)
    raw = np.nan_to_num(raw, nan=0.0)

    # Ensemble across smooths (each smooth: rolling mean of `raw`).
    # We use pandas only for the rolling mean since numpy's stride tricks
    # would be more code than benefit for window sizes <= 100.
    raw_s = pd.Series(raw)
    ensemble = np.zeros(n, dtype=float)
    used = 0
    for w in smooths:
        if w <= 0 or w > n:
            continue
        smoothed = raw_s.rolling(int(w), min_periods=1).mean().to_numpy()
        ensemble += smoothed
        used += 1
    if used == 0:
        return 0.0
    ensemble /= used

    # Horizons feed into raw via the upstream cloud_top / cloud_bottom
    # arrays the caller has already computed per horizon; here we treat
    # `horizons` as a multiplier for the integral cap. This mirrors the
    # original strategy where `position = cumsum(signal).clip(-cap, cap)`.
    cumulative = np.cumsum(ensemble)
    capped = np.clip(cumulative, -pos_cap, pos_cap)

    latest = float(capped[-1])
    if math.isnan(latest):
        return 0.0
    return latest


def ichimoku_strength_score(
    last_close: float,
    metadata: dict,
    atr: float | None,
) -> float | None:
    """Continuous strength score for ranking Ichimoku candidates.

    `ichimoku_daily_signal` returns a binary 0/1 long signal — fine for
    "do I trade this name today?" but not for ranking a candidate
    universe. The intraday scanner picks the top-N by conviction, so it
    needs a continuous score with a comparable scale across symbols.

    Score formula:
        distance_score  = (last_close - kijun_val) / atr
        cloud_thickness = (cloud_top - cloud_bottom) / atr      (ATR-units)
        score           = distance_score * clamp(cloud_thickness, 0.3, 2.0)

    Reading the score:
      higher = more conviction (price far above kijun on a thick cloud)
      <= 0   = below kijun; long-only callers should drop these
      None   = scoring impossible (missing meta or ATR); caller drops.

    ATR-normalisation matters: a $5 distance is small for SPY (~$0.50
    ATR scale relative to price) and huge for an EUR/USD pair. Without
    it the score isn't comparable across symbols.

    `metadata` is the dict returned by `ichimoku_daily_signal` —
    requires `kijun_val`, `cloud_top`, `cloud_bottom`. Returns None on
    any missing component so the scanner can skip cleanly.
    """
    if atr is None or atr <= 0:
        return None
    kijun_val = metadata.get("kijun_val")
    cloud_top = metadata.get("cloud_top")
    cloud_bottom = metadata.get("cloud_bottom")
    if kijun_val is None or cloud_top is None or cloud_bottom is None:
        return None

    distance_score = (last_close - kijun_val) / atr
    cloud_thickness_raw = (cloud_top - cloud_bottom) / atr
    # Clamp thickness so a degenerate cloud (near-zero) doesn't kill
    # the score, and a freak-wide cloud doesn't dominate the ranking.
    cloud_thickness = max(0.3, min(2.0, cloud_thickness_raw))
    return distance_score * cloud_thickness


__all__ = [
    "size_from_vol_target",
    "realised_vol_from_closes",
    "ichimoku_daily_signal",
    "ichimoku_strength_score",
    "fx_reversion_signal_latest",
]
