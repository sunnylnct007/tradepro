"""Convert IBKR MCP tool responses into the bar-dict format used by indicators.py.

The MCP get_price_history response uses parallel arrays (time, open, high, low,
close, volume). This module flattens them into the list-of-dicts that the screener
expects: [{t, o, h, l, c, v}, ...] sorted oldest-first.
"""
from __future__ import annotations

import math
from typing import Any


def _f(val, default: float = 0.0) -> float:
    """Safe float conversion — returns default for None/null."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def bars_from_mcp(history: dict) -> list[dict]:
    """Convert an MCP get_price_history response to screener bar format."""
    times = history.get("time", [])
    opens = history.get("open", [])
    highs = history.get("high", [])
    lows = history.get("low", [])
    closes = history.get("close", [])
    vols = history.get("volume", [])

    n = len(closes)
    bars = []
    for i in range(n):
        bars.append({
            "t": times[i] if i < len(times) else i,
            "o": float(opens[i]) if i < len(opens) else 0.0,
            "h": float(highs[i]) if i < len(highs) else 0.0,
            "l": float(lows[i]) if i < len(lows) else 0.0,
            "c": float(closes[i]),
            "v": float(vols[i]) if i < len(vols) else 0.0,
        })
    return bars  # already oldest-first from IBKR


def snapshot_to_fields(snap: dict) -> dict:
    """Extract key fields from an MCP get_price_snapshot response.

    Returns a flat dict with normalised keys:
      price, high_52w, low_52w, avg_90d_vol, iv_percentile_52w,
      historical_vol_annual, dividend_yield_pct
    """
    last = snap.get("last", {})
    price = _f(last.get("price")) if last else 0.0

    misc = snap.get("misc-statistics", {})
    high_52w = _f(misc.get("high_52w")) if misc else 0.0
    low_52w = _f(misc.get("low_52w")) if misc else 0.0

    vol_data = snap.get("avg-90d-usd-volume", {})
    usd_volume = _f(vol_data.get("volume")) if vol_data else 0.0
    # Convert USD volume to share volume (approx)
    avg_90d_vol = (usd_volume / price) if price else 0.0

    ivp = snap.get("implied-volatility-percentile", {})
    iv_percentile_52w = _f(ivp.get("high_52w")) * 100 if ivp else 0.0

    hv_data = snap.get("historical-vol", {})
    hv_annual = _f(hv_data.get("annual_pct")) * 100 if hv_data else 0.0

    div_data = snap.get("dividend-yield", {})
    div_yield = _f(div_data.get("yield_pct")) if div_data else 0.0

    return {
        "price": price,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "avg_90d_vol": avg_90d_vol,
        "iv_percentile_52w": iv_percentile_52w,
        "historical_vol_annual": hv_annual,
        "dividend_yield_pct": div_yield,
    }


def estimate_put_premium_pct(bars: list[dict], price: float, expiry_days: int = 30) -> float:
    """Black-Scholes ATM put premium estimate as % of stock price.

    Uses the last 30 closes to estimate realised vol, then BS approximation:
    premium ≈ 0.4 × σ × √(T/252) × S
    """
    closes = [float(b["c"]) for b in bars if b.get("c")]
    if len(closes) < 10 or price <= 0:
        return 0.0
    window = closes[-30:] if len(closes) >= 30 else closes
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    daily_vol = (sum(r ** 2 for r in rets) / len(rets)) ** 0.5
    annual_vol = daily_vol * math.sqrt(252)
    premium = 0.4 * annual_vol * math.sqrt(expiry_days / 252) * price
    return round(premium / price * 100, 2)
