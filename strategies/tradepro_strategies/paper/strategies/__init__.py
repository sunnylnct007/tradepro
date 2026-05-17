"""Concrete intraday strategies.

Each strategy lives in its own module (one class per file) so a dev
cloning ORB as a template can copy one file, rename, edit, and have
a working second strategy without touching anything shared.

Strategies opt into the shared plug-in registry via the
`@register_strategy(name)` decorator at class definition. Importing
each module here triggers that registration — so any code doing
`tradepro_strategies.paper.registry.get('orb')` works regardless of
whether the caller has imported the class directly.

Built-in registry keys today:
    orb                       Opening-range breakout (trend / breakout family)
    vwap_mean_reversion       VWAP fade — short above VWAP, long below
    bollinger_bounce          Touch + reject of Bollinger bands
    ma_crossover              Fast/slow EMA crossover (trend follower)

Long-form aliases kept for back-compat with the older intraday
factory:
    opening_range_breakout    same as `orb`
"""
from __future__ import annotations

from typing import Any

from ..registry import (
    get as _registry_get,
    list_names as _registry_list_names,
    register_strategy,
)
from ..strategy import Strategy
from .bollinger_bounce import BollingerBounceIntraday
from .ma_crossover import MovingAverageCrossoverIntraday
from .opening_range_breakout import OpeningRangeBreakout
from .vwap_mean_reversion import VWAPMeanReversion

# Alias the long-form name into the shared registry so legacy callers
# that used `build("opening_range_breakout", ...)` keep working.
register_strategy("opening_range_breakout")(OpeningRangeBreakout)


def build(
    name: str,
    *,
    strategy_id: str,
    params: dict[str, Any] | None = None,
) -> Strategy:
    """Instantiate a strategy by name. Delegates to the shared
    `paper.registry`. Raises KeyError (not ValueError, per registry
    contract) on unknown names so a typo fails loudly."""
    spec = _registry_get(name)
    return spec.build(strategy_id=strategy_id, params=params)


def available() -> list[str]:
    """Names of every registered intraday strategy — in-tree + any
    third-party packages discovered via `tradepro.strategies` entry
    points. Feeds the UI dropdown."""
    return _registry_list_names()


__all__ = [
    "OpeningRangeBreakout",
    "VWAPMeanReversion",
    "BollingerBounceIntraday",
    "MovingAverageCrossoverIntraday",
    "build",
    "available",
]
