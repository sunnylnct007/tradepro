"""Public interface for the macro regime gate.

Downstream models (COMPASS scorer, CATALYST detector, intraday engine)
call `get_risk_mode()` once per session to decide whether to fire signals
and at what sizing fraction.

The underlying computation lives in `market_context.market_context()`.
This module is a thin convenience wrapper so callers don't need to
construct datetime windows or parse the full MarketContext dataclass.

Usage:
    from tradepro_strategies.macro_regime import get_risk_mode, size_multiplier

    mode = get_risk_mode()          # 1, 2, or 3
    mult = size_multiplier(mode)    # 1.0, 0.6, or 0.0
    if mult == 0.0:
        # paper-only — skip live submission
        ...
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .market_context import MarketContext, market_context

_log = logging.getLogger(__name__)

# How far back to pull bars for the regime calculation.
# 252 trading days ≈ 1 year; enough for 52w-high comparisons.
_LOOKBACK_DAYS = 380


@lru_cache(maxsize=1)
def _cached_context(cache_date: str) -> MarketContext:
    """Compute once per calendar day (cache_date is today's ISO date string).
    The lru_cache key flips at midnight so the first call each day refreshes."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    return market_context(start, end)


def get_market_context() -> MarketContext:
    """Return today's MarketContext. Cached for the calendar day."""
    today = datetime.now(timezone.utc).date().isoformat()
    return _cached_context(today)


def get_risk_mode() -> int:
    """Current macro risk mode.

    Returns:
        1 — GREEN: all clear, full position sizing.
        2 — AMBER: caution, reduce new entry sizes to 60%.
        3 — RED:   risk-off, paper-only — no new live entries.

    Falls back to 2 (AMBER) on any fetch failure so the system is
    conservatively cautious rather than blindly permissive.
    """
    try:
        return get_market_context().risk_mode
    except Exception as exc:  # noqa: BLE001
        _log.warning("macro regime fetch failed — defaulting to AMBER: %s", exc)
        return 2


def risk_mode_label(mode: int | None = None) -> str:
    """Human-readable label for a risk mode integer."""
    m = mode if mode is not None else get_risk_mode()
    return {1: "GREEN", 2: "AMBER", 3: "RED"}.get(m, "UNKNOWN")


def size_multiplier(mode: int | None = None) -> float:
    """Position size multiplier for a given risk mode.

    GREEN → 1.0  (full size)
    AMBER → 0.6  (60% of normal — reduce but don't halt)
    RED   → 0.0  (paper-only; caller must not submit live orders)
    """
    m = mode if mode is not None else get_risk_mode()
    return {1: 1.0, 2: 0.6, 3: 0.0}.get(m, 0.6)


def regime_summary() -> str:
    """One-line summary string suitable for logs and the macro strip UI."""
    try:
        ctx = get_market_context()
        return ctx.summary
    except Exception as exc:  # noqa: BLE001
        return f"macro context unavailable: {exc}"


def invalidate_cache() -> None:
    """Force a fresh fetch on the next call — use after a known market event."""
    _cached_context.cache_clear()


__all__ = [
    "get_market_context",
    "get_risk_mode",
    "risk_mode_label",
    "size_multiplier",
    "regime_summary",
    "invalidate_cache",
]
