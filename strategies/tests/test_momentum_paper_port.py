"""The momentum paper sleeve must BE the gated rule, not resemble it.

Two claims are tested here and they are different:
  1. the port shares ONE definition with the screen, so they cannot drift
  2. the sleeve inherits the swing engine's guards rather than copying them

The port's measured result is recorded in MOMENTUM_GATES_V2.md's 24 Sep
amendment: the rule replays at +1.42%/trade over 41,023 trades on the current
969-symbol universe, and FAILS G5 at -36.7% against a -25% bar. That is why the
lane is registered but NOT scheduled.
"""
from __future__ import annotations

from tradepro_strategies.cli import momentum_candidates as screen
from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy)
from tradepro_strategies.paper.strategies.momentum_pullback import (
    MomentumPullbackStrategy)
from tradepro_strategies.signals import momentum_pullback as M


def test_the_paper_entry_IS_the_screen_entry_not_a_copy():
    """One definition. A second copy is how the screen and the lane drift."""
    assert M._entry_signal is screen._entry_signal, (
        "the paper module must delegate to the screen's own _entry_signal; a "
        "retyped copy would let the lane trade a rule the board does not show")


def test_constants_come_from_the_screen():
    for name in ("MAX_HOLD", "STOP_PCT", "TRAIL_PCT"):
        assert getattr(M, name) is getattr(screen, name), f"{name} was retyped"


def test_the_sleeve_inherits_the_guards_rather_than_copying_them():
    """The engine's guards exist because the swing sleeve sold ARWR eleven
    times and went short 252 LRCX. A copied sleeve would have the rule and
    none of the scar tissue."""
    assert issubclass(MomentumPullbackStrategy, MeanReversionSwingStrategy)
    for guard in ("_broker_positions", "_seed_from_oms", "on_bar"):
        assert (getattr(MomentumPullbackStrategy, guard)
                is getattr(MeanReversionSwingStrategy, guard)), (
            f"{guard} was overridden — the whole point is that it is NOT")


def test_only_the_rule_differs():
    assert MomentumPullbackStrategy.SIGNALS is not MeanReversionSwingStrategy.SIGNALS
    assert MomentumPullbackStrategy.SIGNALS.__name__.endswith("momentum_pullback")


def test_momentum_has_NO_target_and_says_so():
    """Mean reversion exits AT a level. Momentum trails. Returning a number
    here would invent one the rule never computes."""
    assert M.target_price([100.0] * 300, 250) is None


def test_the_trailing_stop_reads_the_peak_since_entry():
    closes = [100.0] * 20 + [120.0] + [110.0] * 5     # peak 120 at index 20
    trail = M.trail_price(closes, len(closes) - 1, bars_held=10)
    assert trail is not None
    assert abs(trail - 120.0 * (1 - M.TRAIL_FROM_PEAK)) < 1e-9


def test_exit_order_is_hard_stop_then_trail_then_timeout():
    flat = [100.0] * 60
    # hard stop first: below -8% of fill
    assert M.exit_decision([100.0] * 59 + [90.0], 59,
                           fill_price=100.0, bars_held=5) == (True, "stop")
    # trail: peak 120 since entry, close 108 is below 120*0.92 = 110.4
    closes = [100.0] * 20 + [120.0] + [108.0]
    assert M.exit_decision(closes, 21, fill_price=100.0, bars_held=21)[1] == "trail"
    # timeout last, when nothing else fired
    assert M.exit_decision(flat, 59, fill_price=100.0,
                           bars_held=M.MAX_HOLD) == (True, "timeout")
    assert M.exit_decision(flat, 30, fill_price=100.0, bars_held=5) == (False, None)
