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
    mean_reversion_swing      Swing (2.25 sigma dip above the 200-day)
    momentum_pullback         Momentum (pullback to the 10-day in an uptrend)

Those two are the whole live desk. See the RETIRED block below for what was
deregistered on 10 Oct 2026 and why the files remain.

(vwap_mean_reversion, bollinger_bounce, ma_crossover, compass_momentum were
deleted 21 Aug 2026 — frozen since May, never scheduled, never integrated;
recoverable from git history. ORB stays: paper_session and the backtest CLIs
import it as the reference strategy.)

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
# ── RETIRED, 10 Oct 2026 — DEREGISTERED, NOT DELETED ──────────────────
#
# ichimoku_equity, ichimoku_fx_mr, intraday_flat and orb no longer register.
# Importing a module runs @register_strategy at class definition, so simply
# NOT importing them here is the whole deregistration.
#
# Measured before cutting: of the four, the most recent order from any of
# them was ichimoku_equity on 23 Sep 2026, and NOTHING schedules any of them
# — no launchd job, no Lambda. orb additionally failed its pre-registered
# gates (INTRADAY_VWAP_ORB_GATES_V1, 3 of 5).
#
# They were not harmless while registered. A retired sleeve still in the
# registry produced: strategy rows on the cockpit that read as live, "2
# sleeve(s) whose activity cannot be determined" on the P&L panel, health
# dots for desks that cannot trade, and — on 3 Oct — an Ichimoku cloud-cross
# grading a MOMENTUM position's entry timing as "11 bars LATE" when the
# momentum rule had fired one bar earlier.
#
# The files stay. They are the record of what was built and why it stopped,
# and the two Ichimoku modules carry the only worked example of the stateful
# exit pattern. Deleting them to make a count smaller would lose that; not
# registering them makes them inert, which is the actual goal.
#
# To bring one back: restore its import here and schedule it. That is
# deliberately one line, because a retired strategy returning should be a
# decision, not an accident.
from .opening_range_breakout import OpeningRangeBreakout  # noqa: F401


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
    "build",
    "available",
]

from . import mean_reversion_swing  # noqa: F401 — registers the Swing sleeve
# Momentum: the SAME engine with a different rule (24 Sep 2026). Must be
# imported AFTER mean_reversion_swing — it subclasses it.
from . import momentum_pullback  # noqa: F401 — registers the Momentum sleeve
