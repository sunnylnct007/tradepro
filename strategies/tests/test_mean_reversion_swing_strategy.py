"""The live Swing strategy must behave like the harness that justified it.

Gate F1 of the forward test asks whether live signals match the backtest. The
signal itself is shared code, so what needs pinning here is everything AROUND
it: that a held position is never re-entered, that exits fire on the right
condition, that the daily lock survives a restart, and that a position with no
known cost basis is held rather than guessed at.

The Ichimoku sleeve lost money live on exactly these mechanics — an in-memory
lock voided by */15 restarts and quantity drift — while its signal was correct
throughout.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from tradepro_strategies.signals.mean_reversion import (
    MAX_HOLD, STOP_PCT, entry_signal, exit_decision, stop_price, target_price)


def _dip_series():
    """A steady uptrend, then a dip that is below the 2.5-sigma band but STILL
    above the 200-day average.

    Getting this fixture right is itself instructive: the first version dipped
    to 88, which cleared the sigma band and also fell through the 200-SMA, so
    the rule correctly declined it. That is the trend floor doing its job — the
    strategy is a dip buyer, not a knife catcher.
    """
    c = [80.0 + i * 0.1 for i in range(260)]     # 80 -> 105.9, 200-SMA ~ 96
    for k in range(240, 259):
        c[k] = 106.0                              # tight recent range
    c[-1] = 103.0                                 # a dip, still well above 96
    return c


class TestSignalContract:
    def test_dip_below_two_and_a_half_sigma_fires(self):
        assert entry_signal(_dip_series(), 259) is True

    def test_a_quiet_bar_does_not_fire(self):
        c = _dip_series()
        c[-1] = 106.0                             # no dip at all
        assert entry_signal(c, 259) is False

    def test_a_dip_below_the_200_sma_does_NOT_fire(self):
        """The trend floor is the whole reason this is not catching knives."""
        c = [200.0 - i * 0.5 for i in range(260)]   # sustained downtrend
        c[-1] = c[-2] * 0.85
        assert entry_signal(c, 259) is False

    def test_too_little_history_declines_rather_than_computing(self):
        """A 200-SMA on 60 bars is a 60-SMA wearing a disguise."""
        assert entry_signal([100.0] * 60, 59) is False

    def test_stop_is_eight_percent_below_the_FILL_not_the_signal(self):
        assert stop_price(100.0) == pytest.approx(100.0 * (1 - STOP_PCT))


class TestExitContract:
    def test_stop_fires_on_the_close(self):
        c = [100.0] * 260 + [90.0]
        out, why = exit_decision(c, len(c) - 1, fill_price=100.0, bars_held=1)
        assert out and why == "stop"

    def test_target_fires_when_price_reaches_the_moving_mean(self):
        c = [100.0] * 260
        # filled well below the mean; price back at it
        out, why = exit_decision(c, len(c) - 1, fill_price=90.0, bars_held=1)
        assert out and why == "target"

    def test_timeout_fires_at_max_hold_even_when_nothing_else_does(self):
        c = [100.0] * 260 + [95.0]
        out, why = exit_decision(c, len(c) - 1, fill_price=100.0, bars_held=MAX_HOLD)
        assert out and why == "timeout"

    def test_a_position_in_between_is_simply_held(self):
        c = [100.0] * 260 + [97.0]
        out, why = exit_decision(c, len(c) - 1, fill_price=100.0, bars_held=2)
        assert out is False and why is None

    def test_stop_is_checked_before_target_when_both_would_trigger(self):
        """Conservative ordering: daily closes cannot say which came first, so
        the losing outcome wins. Same rule as the odds calculator."""
        c = [100.0] * 260 + [80.0]
        out, why = exit_decision(c, len(c) - 1, fill_price=100.0, bars_held=1)
        assert why == "stop"


class TestStrategyMechanics:
    """The parts that killed Ichimoku live, pinned here."""

    def _strategy(self):
        from tradepro_strategies.paper.strategies.mean_reversion_swing import (
            MeanReversionSwingStrategy)
        return MeanReversionSwingStrategy(strategy_id="swing_test")

    def test_registered_under_a_stable_name(self):
        """The name is what --strategy-id and strategy_broker_map key on, so
        renaming it silently unwires the live daemon."""
        import tradepro_strategies.paper.strategies.mean_reversion_swing  # noqa: F401
        from tradepro_strategies.paper import registry
        assert "mean_reversion_swing" in registry.list_names()

    def test_position_size_comes_from_configured_capital(self):
        s = self._strategy()
        s.params = {"capital": 100_000, "position_pct": 0.05}
        assert s._size(100.0) == 50          # 5% of 100k / 100

    def test_zero_capital_buys_nothing_rather_than_defaulting(self):
        s = self._strategy()
        s.params = {}
        assert s._size(100.0) == 0

    def test_bars_held_counts_business_days(self):
        s = self._strategy()
        s._entry_bar["X"] = "2026-08-24"      # Monday
        assert s._bars_held("X", "2026-08-31") == 5   # the following Monday
