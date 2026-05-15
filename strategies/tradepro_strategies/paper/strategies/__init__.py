"""Concrete intraday strategies. Each one lives in its own module —
single class per file — so a dev cloning ORB as a template can copy
one file, rename, edit, and have a working second strategy without
touching anything shared.

Registry pattern (analogous to tradepro_strategies.strategies):

    from tradepro_strategies.paper.strategies import build

    s = build("opening_range_breakout", strategy_id="orb_us_lg_001")

This module exposes the build() factory so the engine doesn't need
to know about concrete classes — it asks for a strategy by name +
params and gets back something that implements `Strategy`."""
from __future__ import annotations

from typing import Any

from ..strategy import Strategy
from .opening_range_breakout import OpeningRangeBreakout


_STRATEGY_FACTORIES: dict[str, type[Strategy]] = {
    "opening_range_breakout": OpeningRangeBreakout,
}


def build(
    name: str,
    *,
    strategy_id: str,
    params: dict[str, Any] | None = None,
) -> Strategy:
    """Instantiate a strategy by name. Raises ValueError on unknown
    names so a typo in config fails loudly rather than silently
    skipping the strategy at session-start."""
    cls = _STRATEGY_FACTORIES.get(name)
    if cls is None:
        known = ", ".join(sorted(_STRATEGY_FACTORIES))
        raise ValueError(
            f"unknown intraday strategy {name!r} — known: {known}"
        )
    return cls(strategy_id=strategy_id, params=params or {})


def available() -> list[str]:
    """Names of every registered intraday strategy — feeds a UI
    dropdown the same way the daily comparator's StrategyCatalog
    does for backtests."""
    return sorted(_STRATEGY_FACTORIES)


__all__ = ["OpeningRangeBreakout", "build", "available"]
