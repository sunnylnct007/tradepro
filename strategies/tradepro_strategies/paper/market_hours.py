"""Shared market-hours gate — "is this instrument's venue open right now?"

Strategies should not emit orders into a CLOSED venue: the broker just rejects
them (MARKET_CLOSED) and, since the daemons re-run every few minutes, the same
order is regenerated and re-rejected over and over. Gate emission on this.

Keyed by asset class (resolve with bar_cache.asset_class_resolver):
  us_equity / us_etf : NYSE regular session 09:30–16:00 ET (DST-correct via the
                       America/New_York tz), weekdays, excluding NYSE holidays.
  uk_equity          : LSE 08:00–16:30 London, weekdays.
  fx_spot            : 24/5 — open Sun 21:00 UTC → Fri 21:00 UTC.
  crypto             : always open.
  unknown            : default OPEN (never block on a class we don't model).

NOTE on 24-hour broker CFDs: IG offers "(24 Hours)" US-share CFDs that trade
outside NYSE RTH. A strategy trading THOSE should NOT use the us_equity gate —
it would wrongly skip tradeable hours. Use the broker's market status for those
(follow-up); this helper models the underlying *exchange* calendar.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# Reuse the maintained NYSE holiday set rather than duplicating it.
from ..bar_cache.asset_classes.us_etf import _NYSE_HOLIDAYS

_ET = ZoneInfo("America/New_York")
_LONDON = ZoneInfo("Europe/London")


def _as_utc(ts: datetime) -> datetime:
    """Coerce to an aware UTC datetime (treat naive as UTC)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def is_us_equity_open(ts: datetime) -> bool:
    """NYSE/Nasdaq regular session: 09:30–16:00 ET, Mon–Fri, ex-holidays.
    Half-days (early 13:00 ET close) are treated as open through 16:00 — being
    slightly permissive near a half-day close is harmless (the broker still
    accepts during the real session; we only aim to block the obvious closed
    pre-/post-market + weekends + full holidays that cause the cancel loop)."""
    et = _as_utc(ts).astimezone(_ET)
    if et.weekday() >= 5:
        return False
    if et.date() in _NYSE_HOLIDAYS:
        return False
    return time(9, 30) <= et.time() < time(16, 0)


def is_uk_equity_open(ts: datetime) -> bool:
    """LSE regular session: 08:00–16:30 London, Mon–Fri."""
    ldn = _as_utc(ts).astimezone(_LONDON)
    if ldn.weekday() >= 5:
        return False
    return time(8, 0) <= ldn.time() < time(16, 30)


def is_fx_open(ts: datetime) -> bool:
    """Spot FX 24/5: open Sun 21:00 UTC → Fri 21:00 UTC (DST wobble ignored)."""
    u = _as_utc(ts)
    wd, h = u.weekday(), u.hour  # Mon=0 … Sun=6
    if wd == 5:            # Saturday — closed
        return False
    if wd == 6:            # Sunday — opens ~21:00 UTC
        return h >= 21
    if wd == 4:            # Friday — closes ~21:00 UTC
        return h < 21
    return True            # Mon–Thu


def is_open(asset_class: str, ts: datetime) -> bool:
    """Dispatch by asset class. Unknown classes default OPEN (never block)."""
    ac = (asset_class or "").lower()
    if ac in ("us_equity", "us_etf"):
        return is_us_equity_open(ts)
    if ac == "uk_equity":
        return is_uk_equity_open(ts)
    if ac == "fx_spot":
        return is_fx_open(ts)
    if ac == "crypto":
        return True
    return True
