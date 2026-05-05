"""Family-4 signal: BEAT_AND_RETREAT post-earnings entry pattern.

The classic event-driven setup: a stock beats earnings → rallies →
pulls back 5-15% within a ~60-day window. That pullback IS the
entry signal — the market over-corrected after the rally faded but
the fundamental beat still stands. Day-5-of-60 vs day-55-of-60
matters; freshness decays.

Built on yfinance — no new API keys, no new SDKs. Earnings
announcements are public; yfinance scrapes the same Yahoo pages
the price data comes from. Network calls are wrapped so a fetch
failure produces a graceful no-signal envelope instead of breaking
the comparator.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pandas as pd

WINDOW_DAYS = 60
MIN_RETREAT_PCT = 5.0   # below 5% retreat → not yet a real entry
MAX_RETREAT_PCT = 15.0  # below -15% means the thesis is breaking

_log = logging.getLogger(__name__)


@dataclass
class EarningsEvent:
    """One earnings announcement with the surprise stat we care about."""

    symbol: str
    announce_date: str               # ISO date the report dropped
    eps_estimate: float | None
    eps_actual: float | None
    surprise_pct: float | None       # already a percent (+5.0 = beat by 5%)

    @property
    def beat(self) -> bool | None:
        if self.eps_actual is None or self.eps_estimate is None:
            return None
        return self.eps_actual > self.eps_estimate

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "announce_date": self.announce_date,
            "eps_estimate": self.eps_estimate,
            "eps_actual": self.eps_actual,
            "surprise_pct": self.surprise_pct,
            "beat": self.beat,
        }


def fetch_recent_earnings(
    symbol: str,
    *,
    lookback_days: int = 90,
    ticker_factory=None,
) -> EarningsEvent | None:
    """Most recent earnings event within the lookback window, or None.
    `ticker_factory` is the indirection point for behave to inject a
    fake ticker without hitting the network."""
    try:
        if ticker_factory is None:
            import yfinance as yf
            t = yf.Ticker(symbol)
        else:
            t = ticker_factory(symbol)
        df = t.earnings_dates
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance earnings_dates fetch failed for %s: %s", symbol, e)
        return None

    if df is None or getattr(df, "empty", True):
        return None

    # Drop rows where the company hasn't reported yet (Reported EPS is NaN)
    reported = df.dropna(subset=["Reported EPS"]).sort_index(ascending=False)
    if reported.empty:
        return None

    row = reported.iloc[0]
    when = reported.index[0]
    # Strip tz so we can subtract from a tz-naive 'now' below.
    when_naive = when.tz_convert("UTC").tz_localize(None) if when.tzinfo else when
    days_ago = (datetime.now(timezone.utc).replace(tzinfo=None) - when_naive).days
    if days_ago > lookback_days:
        return None

    return EarningsEvent(
        symbol=symbol.upper(),
        announce_date=when_naive.date().isoformat(),
        eps_estimate=_safe(row.get("EPS Estimate")),
        eps_actual=_safe(row.get("Reported EPS")),
        surprise_pct=_safe(row.get("Surprise(%)")),
    )


def beat_and_retreat_signal(
    symbol: str,
    prices: pd.DataFrame,
    *,
    window_days: int = WINDOW_DAYS,
    ticker_factory=None,
) -> dict:
    """Combines a recent earnings beat with post-earnings price action.

    Verdicts:
      STRONG       — beat AND price retreated 5-15% from post-earnings
                     peak AND we're inside the window
      MODERATE     — beat AND inside the window but retreat not yet 5%
                     OR retreat already exceeded 15%
      EXPIRED      — beat but the window has elapsed
      NO_BEAT      — earnings within lookback but missed estimates
      NO_RECENT    — no earnings within lookback
      NO_PRICES    — earnings found but post-earnings bars missing

    The envelope always carries the diagnostic fields — `fired`
    indicates whether the BUY hint should reach the user.
    """
    base = {
        "_source": f"live://earnings/{symbol.upper()}",
        "fired": False,
        "verdict": "NO_RECENT",
        "earnings": None,
        "days_since_earnings": None,
        "days_remaining_in_window": None,
        "post_earnings_peak": None,
        "current_price": None,
        "retreat_from_post_earnings_peak_pct": None,
    }

    ev = fetch_recent_earnings(symbol, ticker_factory=ticker_factory)
    if ev is None:
        return base
    base["earnings"] = ev.to_dict()
    days_since = (
        datetime.now(timezone.utc).date() - datetime.fromisoformat(ev.announce_date).date()
    ).days
    base["days_since_earnings"] = days_since
    base["days_remaining_in_window"] = max(0, window_days - days_since)

    if ev.beat is False:
        base["verdict"] = "NO_BEAT"
        return base
    if ev.beat is None:
        base["verdict"] = "NO_RECENT"
        return base

    # We have a beat. Slice price history to bars after the announce.
    if prices is None or prices.empty:
        base["verdict"] = "NO_PRICES"
        return base
    series = (
        prices["adj_close"] if "adj_close" in prices.columns else prices["close"]
    )
    after = series[series.index >= pd.Timestamp(ev.announce_date)]
    if after.empty or len(after) < 2:
        base["verdict"] = "NO_PRICES"
        return base
    post_peak = float(after.cummax().iloc[-1])
    current = float(after.iloc[-1])
    retreat_pct = (
        (current - post_peak) / post_peak * 100.0 if post_peak > 0 else 0.0
    )
    base["post_earnings_peak"] = post_peak
    base["current_price"] = current
    base["retreat_from_post_earnings_peak_pct"] = retreat_pct

    if base["days_remaining_in_window"] == 0:
        base["verdict"] = "EXPIRED"
        return base

    # The beat-and-retreat sweet spot: -15% ≤ retreat ≤ -5%.
    in_sweet_spot = -MAX_RETREAT_PCT <= retreat_pct <= -MIN_RETREAT_PCT
    if in_sweet_spot:
        base["verdict"] = "STRONG"
        base["fired"] = True
    else:
        base["verdict"] = "MODERATE"
    return base


def earnings_trace_row(signal: dict) -> dict | None:
    """Decision-trace row representation of the earnings signal so it
    surfaces in the same Compare-expand-panel ladder as RSI / SMA /
    cross-basket. None when there's no recent earnings to discuss."""
    if not signal:
        return None
    verdict = signal.get("verdict")
    if verdict in (None, "NO_RECENT"):
        return None
    days_since = signal.get("days_since_earnings")
    days_left = signal.get("days_remaining_in_window")
    earnings = signal.get("earnings") or {}
    surprise = earnings.get("surprise_pct")
    retreat = signal.get("retreat_from_post_earnings_peak_pct")

    if verdict == "STRONG":
        status = "pass"
    elif verdict == "MODERATE":
        status = "warn"
    elif verdict in ("EXPIRED", "NO_BEAT", "NO_PRICES"):
        status = "fail"
    else:
        status = "warn"

    bits: list[str] = []
    if surprise is not None:
        bits.append(f"beat {surprise:+.1f}%")
    if days_since is not None:
        bits.append(f"day {days_since}/{WINDOW_DAYS}")
    if days_left is not None and verdict not in ("EXPIRED", "NO_BEAT"):
        bits.append(f"{days_left}d remaining")
    if retreat is not None:
        bits.append(f"retreat {retreat:.1f}%")
    detail = (
        f"{verdict.lower().replace('_', ' ')} — " + ", ".join(bits)
        if bits else verdict.lower().replace("_", " ")
    )

    return {
        "name": "Earnings beat-and-retreat",
        "status": status,
        "detail": detail,
    }


def _safe(x: Any) -> float | None:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f
