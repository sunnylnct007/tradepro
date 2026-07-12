"""Swing trade screener — gate criteria + scoring per SRS Section 9."""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass

from indicators import (
    avg_volume, recent_avg_volume, rsi, sma, spy_return_20d, stock_return_20d
)

log = logging.getLogger("screener.swing")

EARNINGS_BLACKOUT_DAYS = 5
VOLUME_MIN = 500_000
RSI_MAX_GATE = 75
PRICE_MIN = 10.0


@dataclass
class SwingCandidate:
    ticker: str
    price: float
    score: int
    rsi_val: float
    ma20_dist_pct: float    # % distance from 20d MA (negative = below)
    vol_signal: str          # e.g. "1.4x avg"
    trend_status: str
    rs_status: str           # "outperforming SPY" / "underperforming SPY"
    gate_fail_reason: str = ""
    dual_candidate: bool = False

    def passed_gate(self) -> bool:
        return not self.gate_fail_reason


def score_swing(
    ticker: str,
    price: float,
    bars: list[dict],
    spy_bars: list[dict],
    earnings_date: str | None,
) -> SwingCandidate:
    """Apply gate criteria then score. Returns SwingCandidate (check .gate_fail_reason)."""

    # --- Gate ---
    if earnings_date:
        days_to = (datetime.date.fromisoformat(earnings_date[:10]) - datetime.date.today()).days
        if 0 <= days_to <= EARNINGS_BLACKOUT_DAYS:
            return _fail(ticker, price, f"earnings in {days_to}d (blackout {EARNINGS_BLACKOUT_DAYS}d)")

    avg_vol = avg_volume(bars, 90)
    if avg_vol < VOLUME_MIN:
        return _fail(ticker, price, f"avg vol {avg_vol:,.0f} < {VOLUME_MIN:,}")

    rsi_val = rsi(bars, 14)
    if rsi_val is None:
        return _fail(ticker, price, "insufficient bars for RSI")
    if rsi_val >= RSI_MAX_GATE:
        return _fail(ticker, price, f"RSI {rsi_val:.1f} >= {RSI_MAX_GATE} (extended)")

    if price < PRICE_MIN:
        return _fail(ticker, price, f"price ${price:.2f} < ${PRICE_MIN}")

    # --- Score ---
    score = 0

    # S-S1 Trend
    ma50 = sma(bars, 50)
    ma200 = sma(bars, 200)
    above50 = ma50 and price > ma50
    above200 = ma200 and price > ma200
    if above50 and above200:
        score += 3
        trend_status = "ABOVE 50d and 200d MA"
    elif above50:
        score += 2
        trend_status = "ABOVE 50d MA only"
    else:
        trend_status = "BELOW 50d MA"

    # S-S2 Pullback quality (distance from 20d MA)
    ma20 = sma(bars, 20)
    if ma20:
        ma20_dist = (price - ma20) / ma20 * 100
    else:
        ma20_dist = 0.0
    abs_dist = abs(ma20_dist)
    if abs_dist <= 3:
        score += 3
    elif abs_dist <= 8:
        score += 1

    # S-S3 RSI momentum
    if 40 <= rsi_val <= 60:
        score += 3
    elif 60 < rsi_val <= 70:
        score += 2
    elif 30 <= rsi_val < 40:
        score += 1

    # S-S4 Volume signal
    avg_vol_20d = avg_volume(bars, 20)
    recent_vol = recent_avg_volume(bars, 3)
    if avg_vol_20d > 0:
        vol_ratio = recent_vol / avg_vol_20d
        if vol_ratio >= 1.3:
            score += 3
            vol_signal = f"{vol_ratio:.1f}x avg"
        elif vol_ratio >= 1.1:
            score += 1
            vol_signal = f"{vol_ratio:.1f}x avg"
        else:
            vol_signal = f"{vol_ratio:.1f}x avg"
    else:
        vol_ratio = 0.0
        vol_signal = "n/a"

    # S-S5 Relative strength vs SPY over 20d
    stock_ret = stock_return_20d(bars)
    spy_ret = spy_return_20d(spy_bars)
    if stock_ret > spy_ret:
        score += 2
        rs_status = "outperforming SPY"
    else:
        rs_status = "underperforming SPY"

    # Minimum score gate
    if score < 5:
        return _fail(ticker, price, f"score {score}/14 < minimum 5")

    return SwingCandidate(
        ticker=ticker,
        price=price,
        score=score,
        rsi_val=round(rsi_val, 1),
        ma20_dist_pct=round(ma20_dist, 1),
        vol_signal=vol_signal,
        trend_status=trend_status,
        rs_status=rs_status,
    )


def _fail(ticker: str, price: float, reason: str) -> SwingCandidate:
    log.info("%s swing FAIL: %s", ticker, reason)
    return SwingCandidate(
        ticker=ticker, price=price, score=0, rsi_val=0, ma20_dist_pct=0,
        vol_signal="", trend_status="", rs_status="", gate_fail_reason=reason,
    )
