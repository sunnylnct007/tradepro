"""Rule-based technical analysis text for wheel and swing candidates."""
from __future__ import annotations


def wheel_analysis(c) -> str:
    parts = []

    # IV / premium
    if c.ivr >= 70:
        parts.append(f"IV rank is elevated at {c.ivr:.0f}% — premium is rich, ideal for selling puts.")
    elif c.ivr >= 40:
        parts.append(f"IV rank at {c.ivr:.0f}% offers decent premium for put selling.")
    else:
        parts.append(f"IV rank is low ({c.ivr:.0f}%) — premium is thin; size down or wait for a vol spike.")

    # Dividend
    if c.div_yield >= 4.0:
        parts.append(f"High dividend yield of {c.div_yield:.1f}% provides downside cushion if assigned.")
    elif c.div_yield >= 1.5:
        parts.append(f"Dividend yield of {c.div_yield:.1f}% adds income if assigned.")

    # Distance from 52w low
    if c.dist_52w_low_pct <= 15:
        parts.append(f"Stock is only {c.dist_52w_low_pct:.1f}% above its 52-week low — elevated assignment risk; use tight strikes.")
    elif c.dist_52w_low_pct <= 30:
        parts.append(f"Trading {c.dist_52w_low_pct:.1f}% above 52w low — moderate downside buffer.")
    else:
        parts.append(f"Well off lows ({c.dist_52w_low_pct:.1f}% above 52w low) — comfortable downside cushion.")

    # Trend
    parts.append(f"Trend: {c.trend_status}.")

    # Premium estimate
    if c.premium_pct >= 3.0:
        parts.append(f"Estimated ATM put premium ~{c.premium_pct:.1f}% — strong yield for a 30-day cycle.")
    elif c.premium_pct >= 1.5:
        parts.append(f"Estimated put premium ~{c.premium_pct:.1f}% for 30 days — acceptable.")
    else:
        parts.append(f"Put premium estimate is thin (~{c.premium_pct:.1f}%) — consider wider expiry.")

    return " ".join(parts)


def swing_analysis(c) -> str:
    parts = []

    # RSI
    if c.rsi_val <= 35:
        parts.append(f"RSI at {c.rsi_val:.1f} — oversold territory, potential mean-reversion bounce.")
    elif c.rsi_val <= 50:
        parts.append(f"RSI at {c.rsi_val:.1f} — pulling back from highs, momentum resetting.")
    elif c.rsi_val <= 65:
        parts.append(f"RSI at {c.rsi_val:.1f} — neutral-to-bullish momentum, not yet extended.")
    else:
        parts.append(f"RSI at {c.rsi_val:.1f} — momentum is strong but approaching overbought; manage risk carefully.")

    # MA distance
    if c.ma20_dist_pct < -5:
        parts.append(f"Price is {abs(c.ma20_dist_pct):.1f}% below the 20d MA — significant pullback, watch for bounce at support.")
    elif c.ma20_dist_pct < 0:
        parts.append(f"Price dipped {abs(c.ma20_dist_pct):.1f}% below the 20d MA — mild pullback, potential entry zone.")
    elif c.ma20_dist_pct <= 3:
        parts.append(f"Hugging the 20d MA (+{c.ma20_dist_pct:.1f}%) — consolidating; breakout above confirms continuation.")
    else:
        parts.append(f"Extended {c.ma20_dist_pct:.1f}% above 20d MA — momentum is strong but consider waiting for a pullback to MA.")

    # Relative strength vs SPY
    if "outperforming" in c.rs_status:
        parts.append("Outperforming SPY on 20-day relative strength — sector rotation favourable.")
    else:
        parts.append("Underperforming SPY on relative strength — confirm sector support before entry.")

    # Volume
    parts.append(f"Volume at {c.vol_signal} — {'above average confirms conviction.' if 'x' in c.vol_signal and float(c.vol_signal.split('x')[0]) >= 1.2 else 'in line with average.'}")

    # Trend
    parts.append(f"Structure: {c.trend_status}.")

    return " ".join(parts)
