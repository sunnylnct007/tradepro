"""Claude API integration for 2-sentence candidate explanations (SRS Section 11)."""
from __future__ import annotations

import logging

log = logging.getLogger("screener.claude")

_FALLBACK = "See metrics above."
_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 150

_WHEEL_SYSTEM = "You are a concise options trading assistant. Respond in exactly 2 sentences."
_SWING_SYSTEM = "You are a concise swing trading assistant. Respond in exactly 2 sentences."


def explain_wheel(
    client,
    ticker: str,
    price: float,
    ivr: float,
    div_yield: float,
    score: int,
    volatility_label: str,
    close_guidance: str,
    trend_status: str,
    dist_52w_low_pct: float,
) -> str:
    prompt = (
        f"Explain why {ticker} is a good wheel candidate today.\n"
        f"Price: ${price:.2f} | IV Rank: {ivr:.0f}% | Dividend: {div_yield:.1f}%\n"
        f"Score: {score}/14 | Volatility: {volatility_label} | Close guidance: {close_guidance}\n"
        f"Trend: {trend_status} | Distance from 52w low: {dist_52w_low_pct:.1f}%"
    )
    return _call(client, _WHEEL_SYSTEM, prompt, ticker)


def explain_swing(
    client,
    ticker: str,
    price: float,
    rsi_val: float,
    ma20_dist_pct: float,
    score: int,
    trend_status: str,
    vol_signal: str,
    rs_status: str,
) -> str:
    prompt = (
        f"Explain why {ticker} is a good swing candidate today.\n"
        f"Price: ${price:.2f} | RSI: {rsi_val:.1f} | vs 20d MA: {ma20_dist_pct:+.1f}%\n"
        f"Score: {score}/14 | Trend: {trend_status}\n"
        f"Volume signal: {vol_signal} | vs SPY 20d: {rs_status}"
    )
    return _call(client, _SWING_SYSTEM, prompt, ticker)


def _call(client, system: str, prompt: str, ticker: str) -> str:
    try:
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()
    except Exception as e:  # noqa: BLE001
        log.warning("%s Claude API failed: %s — using fallback", ticker, e)
        return _FALLBACK
