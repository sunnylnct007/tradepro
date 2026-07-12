"""Technical indicator calculations from raw OHLCV bar lists.

All functions accept a list of dicts with keys {t, o, h, l, c, v}
(IBKR history format) sorted oldest-first.
"""
from __future__ import annotations

import math
from typing import Sequence


def _closes(bars: list[dict]) -> list[float]:
    return [float(b["c"]) for b in bars if b.get("c")]


def _volumes(bars: list[dict]) -> list[float]:
    return [float(b["v"]) for b in bars if b.get("v")]


def sma(bars: list[dict], period: int) -> float | None:
    closes = _closes(bars)
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(bars: list[dict], period: int = 14) -> float | None:
    closes = _closes(bars)
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # Use only the last `period` changes for initial RS
    g = gains[-period:]
    l_ = losses[-period:]
    avg_g = sum(g) / period
    avg_l = sum(l_) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def avg_volume(bars: list[dict], days: int = 20) -> float:
    vols = _volumes(bars)
    if not vols:
        return 0.0
    recent = vols[-days:] if len(vols) >= days else vols
    return sum(recent) / len(recent)


def recent_avg_volume(bars: list[dict], days: int = 3) -> float:
    vols = _volumes(bars)
    if not vols:
        return 0.0
    recent = vols[-days:] if len(vols) >= days else vols
    return sum(recent) / len(recent)


def spy_return_20d(spy_bars: list[dict]) -> float:
    """SPY 20-day return (fraction)."""
    closes = _closes(spy_bars)
    if len(closes) < 21:
        return 0.0
    return (closes[-1] - closes[-21]) / closes[-21]


def stock_return_20d(bars: list[dict]) -> float:
    closes = _closes(bars)
    if len(closes) < 21:
        return 0.0
    return (closes[-1] - closes[-21]) / closes[-21]


def iv_rank(bars: list[dict], current_iv: float) -> float | None:
    """IV rank over 1 year of daily bars using HV as proxy (no IV history available).

    Returns 0–100 float or None if insufficient data.
    """
    closes = _closes(bars)
    if len(closes) < 21:
        return None
    # Rolling 21d HV samples
    hvs = []
    for i in range(20, len(closes)):
        window = closes[i - 20: i + 1]
        rets = [math.log(window[j] / window[j - 1]) for j in range(1, len(window))]
        daily_vol = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5
        hvs.append(daily_vol * math.sqrt(252) * 100)
    if not hvs:
        return None
    lo, hi = min(hvs), max(hvs)
    if hi == lo:
        return 50.0
    return round((current_iv - lo) / (hi - lo) * 100, 1)
