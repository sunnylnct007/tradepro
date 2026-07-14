"""Wheel strategy screener — gate criteria + scoring per SRS Section 8."""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field

from indicators import (
    avg_volume, iv_rank, rsi, sma, stock_return_20d
)

log = logging.getLogger("screener.wheel")

EARNINGS_BLACKOUT_DAYS = 21
PRICE_MIN = 15.0
PRICE_MAX = 250.0
OPTION_OI_MIN = 500
VOLUME_MIN = 200_000


@dataclass
class WheelCandidate:
    ticker: str
    price: float
    score: int
    ivr: float         # IV rank 0–100
    div_yield: float   # %
    trend_status: str  # e.g. "above 50d and 200d MA"
    dist_52w_low_pct: float
    premium_pct: float
    volatility_label: str    # Conservative / Medium / Volatile
    close_guidance: str
    suggested_strike: float
    suggested_expiry: str
    gate_fail_reason: str = ""
    dual_candidate: bool = False
    # Real option data (populated when options block present in JSON)
    option_expiry: str = ""
    atm_put_bid: float = 0.0
    atm_put_ask: float = 0.0
    atm_put_mid: float = 0.0
    atm_put_iv_pct: float = 0.0
    atm_put_oi: int = 0
    option_chain: list = field(default_factory=list)
    # Real underlying IV and option liquidity from snapshot
    current_iv_pct: float = 0.0
    avg_option_volume: float = 0.0

    def passed_gate(self) -> bool:
        return not self.gate_fail_reason


def volatility_label_and_guidance(ivr: float) -> tuple[str, str]:
    if ivr < 25:
        return "Conservative", "Hold to expiry or 75-80% profit"
    if ivr <= 45:
        return "Medium", "Close at 60-65% profit"
    return "Volatile", "Close at 50% profit — gamma risk"


def score_wheel(
    ticker: str,
    price: float,
    ivr: float,
    div_yield: float,
    bars: list[dict],
    low_52w: float,
    high_52w: float,
    premium_pct: float,
    earnings_date: str | None,
    options: dict | None = None,
    current_iv_pct: float = 0.0,
    avg_option_volume: float = 0.0,
) -> WheelCandidate:
    """Apply gate criteria then score. Returns WheelCandidate (check .gate_fail_reason)."""

    # --- Gate ---
    if earnings_date:
        days_to = (datetime.date.fromisoformat(earnings_date[:10]) - datetime.date.today()).days
        if 0 <= days_to <= EARNINGS_BLACKOUT_DAYS:
            return _fail(ticker, price, f"earnings in {days_to}d (blackout {EARNINGS_BLACKOUT_DAYS}d)")

    if not (PRICE_MIN <= price <= PRICE_MAX):
        return _fail(ticker, price, f"price ${price:.2f} outside ${PRICE_MIN}–${PRICE_MAX}")

    avg_vol = avg_volume(bars, 90)
    if avg_vol < VOLUME_MIN:
        return _fail(ticker, price, f"avg vol {avg_vol:,.0f} < {VOLUME_MIN:,}")

    # OI gate: check real option OI when available
    if options and options.get("atm_put"):
        atm_oi = options["atm_put"].get("open_interest", 0) or 0
        if atm_oi < OPTION_OI_MIN:
            return _fail(ticker, price, f"ATM put OI {atm_oi} < {OPTION_OI_MIN}")

    # --- Score ---
    score = 0

    # S-W1 IV Rank
    if ivr > 40:
        score += 3
    elif ivr >= 20:
        score += 2
    elif ivr >= 10:
        score += 1

    # S-W2 Dividend yield
    if div_yield > 4:
        score += 3
    elif div_yield >= 2:
        score += 2
    elif div_yield > 0:
        score += 1

    # S-W3 Trend stability
    ma50 = sma(bars, 50)
    ma200 = sma(bars, 200)
    above50 = ma50 and price > ma50
    above200 = ma200 and price > ma200
    if above50 and above200:
        score += 3
        trend_status = "above 50d and 200d MA"
    elif above50 or above200:
        score += 1
        trend_status = "above one MA only"
    else:
        trend_status = "below both MAs"

    # S-W4 Distance from 52w low
    dist_pct = ((price - low_52w) / low_52w * 100) if low_52w else 0
    if dist_pct > 25:
        score += 3
    elif dist_pct >= 15:
        score += 2
    elif dist_pct >= 5:
        score += 1

    # S-W5 Premium — use real ATM put mid if available, else BS estimate
    real_atm = options.get("atm_put") if options else None
    if real_atm and real_atm.get("mid") and price > 0:
        real_premium_pct = real_atm["mid"] / price * 100
        premium_pct = round(real_premium_pct, 2)
    if premium_pct > 2:
        score += 2
    elif premium_pct >= 1:
        score += 1

    # Minimum score gate
    if score < 5:
        return _fail(ticker, price, f"score {score}/14 < minimum 5")

    vol_label, close_guidance = volatility_label_and_guidance(ivr)

    # Suggested strike and expiry — use real option data when available
    if real_atm:
        suggested_strike = float(real_atm.get("strike", round(price * 0.95)))
    else:
        suggested_strike = round(price * 0.95)

    if options and options.get("expiry"):
        try:
            exp_date = datetime.date.fromisoformat(options["expiry"])
            suggested_expiry = exp_date.strftime("%d %b %Y")
        except ValueError:
            suggested_expiry = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%d %b %Y")
    else:
        suggested_expiry = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%d %b %Y")

    return WheelCandidate(
        ticker=ticker,
        price=price,
        score=score,
        ivr=ivr,
        div_yield=div_yield,
        trend_status=trend_status,
        dist_52w_low_pct=round(dist_pct, 1),
        premium_pct=premium_pct,
        volatility_label=vol_label,
        close_guidance=close_guidance,
        suggested_strike=suggested_strike,
        suggested_expiry=suggested_expiry,
        option_expiry=options.get("expiry", "") if options else "",
        atm_put_bid=float(real_atm["bid"]) if real_atm and real_atm.get("bid") else 0.0,
        atm_put_ask=float(real_atm["ask"]) if real_atm and real_atm.get("ask") else 0.0,
        atm_put_mid=float(real_atm["mid"]) if real_atm and real_atm.get("mid") else 0.0,
        atm_put_iv_pct=float(real_atm["iv_pct"]) if real_atm and real_atm.get("iv_pct") else 0.0,
        atm_put_oi=int(real_atm["open_interest"]) if real_atm and real_atm.get("open_interest") else 0,
        option_chain=options.get("chain", []) if options else [],
        current_iv_pct=current_iv_pct,
        avg_option_volume=avg_option_volume,
    )


def _fail(ticker: str, price: float, reason: str) -> WheelCandidate:
    log.info("%s wheel FAIL: %s", ticker, reason)
    return WheelCandidate(
        ticker=ticker, price=price, score=0, ivr=0, div_yield=0,
        trend_status="", dist_52w_low_pct=0, premium_pct=0,
        volatility_label="", close_guidance="", suggested_strike=0,
        suggested_expiry="", gate_fail_reason=reason,
    )
