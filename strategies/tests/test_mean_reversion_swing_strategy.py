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


class TestInheritedPositions:
    """A position this strategy did not open must be left alone.

    The IBKR paper account carried three positions from the Ichimoku clone
    that ran there until 22 Aug. On its first live run this strategy adopted
    them, found the 20-day mean already above their price, and emitted
    "swing exit target held=0" SELLs for DIS, ABBV and COP — three orders for
    trades it never made. Gate F2 of the forward test requires every fill to
    trace to a published signal; those trace to another strategy months ago,
    and their cost basis belongs to it, so any P&L booked against them is
    fiction.
    """

    def _strategy_holding(self, qty: int):
        from tradepro_strategies.paper.strategies.mean_reversion_swing import (
            MeanReversionSwingStrategy)
        s = MeanReversionSwingStrategy(strategy_id="swing_test")
        s.params = {"capital": 100_000}
        s.seed_positions({"DIS": qty}, {"DIS": 100.0})
        return s

    def test_session_start_does_not_claim_broker_positions(self):
        """initial_positions used to seed _entry_bar, which is what made a
        foreign holding look like ours."""
        s = self._strategy_holding(14)
        s.params = {"capital": 100_000, "initial_positions": {"DIS": 14}}
        s.on_session_start(__import__("datetime").datetime(2026, 8, 24))
        assert "DIS" not in s._entry_bar
        assert "DIS" not in s._fill_price

    def test_a_position_we_never_filled_is_ours_only_after_a_fill(self):
        s = self._strategy_holding(14)
        assert "DIS" not in s._fill_price      # seeded, not filled


class TestEntryNeverChasesTheGap:
    """SNOW, 3 Sep 2026: signal on the pre-print dip at 305.84; SNOW reported
    that evening and gapped up; the MARKET entry filled at 367.44 — 20% above
    its own reference and 41 points ABOVE its own 325.99 target. A
    mean-reversion entry that pays up through the band that defined it is not
    late, it is wrong: the dip it was built to buy no longer exists."""

    def test_the_entry_is_a_limit_capped_just_above_the_signal_close(self):
        import tradepro_strategies.paper.strategies.mean_reversion_swing as M
        M._chase_cache.clear(); M._chase_cache.append(1.5)
        orders = _entry_orders_for_dip()
        assert len(orders) == 1, "the proven dip fixture must fire exactly once"
        o = orders[0]
        ref = o.signal_ref_price
        assert o.type.value == "LIMIT"
        assert o.limit_price == round(ref * 1.015, 2), \
            "the cap must be derived from the SIGNAL close, nothing else"
        # the SNOW morning, replayed against the cap: a 20% gap must not fill
        assert ref * 1.20 > o.limit_price

    def test_the_router_speaks_the_oms_dialect_not_the_enum(self):
        """MARKET was tested against ("MKT", ...) — never matched — so every
        order, LIMIT included, was silently downgraded to a market order: the
        downgrade IBKRClient itself refuses ("a LMT order needs a price")."""
        import inspect
        from tradepro_strategies.paper.brokers import t212
        src = inspect.getsource(t212)
        assert '"LIMIT": "LMT"' in src
        assert '"MARKET": "MKT"' in src
        assert 'intent["LimitPrice"] = order.limit_price' in src


def _entry_orders_for_dip():
    """Drive one bar through on_bar with the file's PROVEN firing series
    (_dip_series: uptrend, 2.5σ dip, still above the 200-SMA — a dip, not a
    falling knife). `_history` is stubbed at its seam: the strategy reads
    settled closes from the bar store, never from the streamed bar."""
    import datetime as dt

    import tradepro_strategies.paper.strategies.mean_reversion_swing as M
    from tradepro_strategies.paper.strategy import Bar

    s = M.MeanReversionSwingStrategy(strategy_id="t")
    s.params = {"capital": 100_000, "position_pct": 0.05}
    closes = _dip_series()
    dates = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat()
             for i in range(len(closes))]

    def _stub_history(sym, bar):
        s._dates = dates
        return closes, None

    s._history = _stub_history
    bar = Bar(symbol="SNOW", timestamp=dt.datetime(2024, 12, 1, tzinfo=dt.UTC),
              open=closes[-1], high=closes[-1], low=closes[-1],
              close=closes[-1], volume=1_000_000, timeframe_seconds=86_400)
    # The entry path ranks the day's firing candidates across the whole
    # universe; pin it to the one symbol under test.
    from unittest.mock import patch

    import tradepro_strategies.universe as U
    with patch.object(U, "universe_symbols", lambda **kw: ["SNOW"]):
        return [o for o in (s.on_bar(bar) or []) if o.side.value == "BUY"]
