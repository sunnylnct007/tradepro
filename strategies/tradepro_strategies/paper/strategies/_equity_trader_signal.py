"""Faithful port of the trader's daily Ichimoku long/flat equity signal.

VERBATIM copy of the signal math from the trader's `docs/strategy.py`
(IchimokuStrategy.compute_indicators + compute_position) and the params from
`docs/config 4.py`. Single source of truth for the live `ichimoku_equity`
strategy: it calls `latest_position()` on its rolling daily window and acts on
the last value, so the live signal cannot drift from the trader's spec.

WHY (same lesson as FX): the previous live signal (signal_bridge.
ichimoku_daily_signal) had drifted — it added a 3rd entry condition
(close > tenkan) the trader doesn't have, and it was STATELESS (recomputed 1/0
each bar) instead of the trader's STATEFUL ffill long/flat. Stateless loses the
cloud-thickness hysteresis: the trader holds a long while price sits inside the
cloud and only exits below cloud_BOTTOM, whereas the stateless version dropped
the moment price fell below cloud_TOP, then re-entered → churn.

`tests/test_equity_signal_parity.py` pins this module against an independent
copy of docs/strategy.py so any future drift fails the build. If the trader
revises the spec, update docs + here + the golden copy together.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ── trader params (verbatim from docs/config 4.py) ───────────────────
TENKAN = 5
KIJUN = 32
SENKOU_B = 50
DISPLACEMENT = 32

# Indicators need senkou_b + displacement bars before the cloud forms.
MIN_BARS = SENKOU_B + DISPLACEMENT + 1  # = 83


def compute_indicators(df: pd.DataFrame, tenkan: int = TENKAN, kijun: int = KIJUN,
                       senkou_b: int = SENKOU_B, displacement: int = DISPLACEMENT) -> pd.DataFrame:
    """Add tenkan, kijun, span_a/b, cloud_top/bottom columns (verbatim)."""
    df = df.copy()
    df["tenkan"] = (
        df["High"].rolling(tenkan).max() + df["Low"].rolling(tenkan).min()
    ) / 2
    df["kijun"] = (
        df["High"].rolling(kijun).max() + df["Low"].rolling(kijun).min()
    ) / 2
    df["span_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(displacement)
    df["span_b"] = ((
        df["High"].rolling(senkou_b).max()
        + df["Low"].rolling(senkou_b).min()
    ) / 2).shift(displacement)
    df["cloud_top"] = df[["span_a", "span_b"]].max(axis=1)
    df["cloud_bottom"] = df[["span_a", "span_b"]].min(axis=1)
    return df


def compute_position(df: pd.DataFrame) -> pd.DataFrame:
    """Stateful long/flat. ENTRY: Close>cloud AND TK bullish. EXIT: opposite (verbatim)."""
    df = df.copy()
    long_cond = (df["Close"] > df["cloud_top"]) & (df["tenkan"] > df["kijun"])
    flat_cond = (df["Close"] < df["cloud_bottom"]) | (df["tenkan"] < df["kijun"])
    pos = pd.Series(np.nan, index=df.index)
    pos[long_cond] = 1.0
    pos[flat_cond] = 0.0
    df["position"] = pos.ffill().fillna(0.0)
    return df


# ── streaming wrapper used by the live strategy ──────────────────────
def position_series(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> pd.Series:
    """Run the trader's pipeline over a daily window → stateful long/flat series."""
    df = pd.DataFrame({"High": np.asarray(highs, float),
                       "Low": np.asarray(lows, float),
                       "Close": np.asarray(closes, float)})
    return compute_position(compute_indicators(df))["position"]


def latest_position(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> float:
    """The latest long/flat the trader's logic holds (1.0 long, 0.0 flat).
    Returns 0.0 if there isn't enough history."""
    if len(closes) < MIN_BARS:
        return 0.0
    v = float(position_series(closes, highs, lows).iloc[-1])
    return 0.0 if np.isnan(v) else v


def sleeve_weight(n_signal: int, sleeve_size: int) -> float:
    """Trader's per-name sleeve weight for the CURRENT bar (docs/portfolio 1.py).

    The trader sizes each signalling name at 1/sleeve_size, then scales the
    whole sleeve so it is AT MOST 100% invested:
        raw_w  = 1 / sleeve_size          (per signalling name)
        scale  = min(1, 1 / Σ raw_w)
        weight = raw_w * scale
    Closed form per name = 1 / max(sleeve_size, n_signal). So sleeve_size is a
    SIZING denominator, NOT a position-count cap — the trader holds every
    signalling name, scaled down as more signal (never a top-N cut)."""
    if n_signal <= 0:
        return 0.0
    return 1.0 / max(int(sleeve_size), int(n_signal))


def latest_signal_and_meta(closes: np.ndarray, highs: np.ndarray,
                           lows: np.ndarray) -> tuple[float, dict]:
    """Drop-in for the old signal_bridge.ichimoku_daily_signal: returns the
    trader's stateful long/flat (1.0/0.0) AND the cloud/TK metadata the
    strategy uses for conviction ranking + UI tags."""
    if len(closes) < MIN_BARS:
        return 0.0, {}
    df = compute_position(compute_indicators(
        pd.DataFrame({"High": np.asarray(highs, float),
                      "Low": np.asarray(lows, float),
                      "Close": np.asarray(closes, float)})))
    last = df.iloc[-1]
    ct = last["cloud_top"]
    cb = last["cloud_bottom"]
    last_close = float(last["Close"])
    if pd.isna(ct) or pd.isna(cb):
        cloud_position = "unknown"
    elif last_close > ct:
        cloud_position = "above"
    elif last_close < cb:
        cloud_position = "below"
    else:
        cloud_position = "in"
    pos = float(last["position"])
    meta = {
        "cloud_top": float(ct) if not pd.isna(ct) else None,
        "cloud_bottom": float(cb) if not pd.isna(cb) else None,
        "tenkan_val": float(last["tenkan"]) if not pd.isna(last["tenkan"]) else None,
        "kijun_val": float(last["kijun"]) if not pd.isna(last["kijun"]) else None,
        "cloud_position": cloud_position,
    }
    return (0.0 if np.isnan(pos) else pos), meta
