"""Momentum pullback, on paper — the gated rule's first contact with a broker.

Owner, 24 Sep 2026: *"unless we start booking these trades how will we know if
our strategy is really working or not"*.

The momentum rule passed its pre-registered gates in August — 5,815 trades,
47.0% win, +1.53% per trade, all six gates — and has never placed an order. It
has carried a GATED badge on a board next to Swing, whose badge is backed by a
backtest AND live fills. Those are not the same claim, and only booking the
trades can settle it.

WHY THIS FILE IS SHORT, and must stay short. The swing sleeve is 727 lines and
about twenty of them are its rule. The rest is what was expensive to learn:
exits that FAIL CLOSED when the broker cannot be read, every sell verified
against broker positions first, inherited positions left alone, OMS seeding,
the chase band, the placement window. Each exists because something went wrong
once — ARWR sold eleven times, 252 LRCX short on a long-only desk, entries
blocked by phantom positions.

Copying that file would have given this sleeve the rule and NONE of the scar
tissue, and duplicate definitions are already this desk's most common bug
shape. So the rule moved behind a swappable SIGNALS attribute and this sleeve
subclasses: it swaps the twenty lines and inherits all 707. Any guard fixed in
the parent is fixed here on the same commit, which is the whole point.
"""
from __future__ import annotations

from ...signals import momentum_pullback as _mom_signals
from ..registry import register_strategy
from .mean_reversion_swing import MeanReversionSwingStrategy


@register_strategy("momentum_pullback")
class MomentumPullbackStrategy(MeanReversionSwingStrategy):
    """Swing's engine, momentum's rule. Nothing else differs."""

    SIGNALS = _mom_signals
    RULE_LABEL = "momentum pullback"
