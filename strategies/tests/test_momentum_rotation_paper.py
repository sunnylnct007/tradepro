"""The momentum rotation's paper sleeve trades the shared decision through
swing's chassis (MOMENTUM_ROTATION_GATES_V1.md, spec 12 Nov 2026)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradepro_strategies.paper.strategies.momentum_rotation import MomentumRotationStrategy
from tradepro_strategies.paper.strategy import Bar, OrderSide

DEC = {"signal_date": "2026-11-13", "order_date": "2026-11-16",
       "next_signal_date": "2026-12-15", "picks": ["NVDA", "MU"],
       "scores": {"NVDA": 0.9, "MU": 0.7}, "weight_each": 0.05,
       "allocation": 0.5, "portfolio_vol": 0.3, "reason": "2 picks"}


def _s(today="2026-11-16", sym="NVDA", dec=DEC, sold=frozenset()):
    s = MomentumRotationStrategy(strategy_id="momentum_rotation_ibkr",
                                 params={"capital": 100_000})
    s._today, s._cur = today, sym
    s._dec_cache[today] = dec
    s._sold_cache_day, s._sold_cache = today, (None if sold is None else set(sold))
    return s


def test_enters_a_pick_from_the_order_day():
    assert _s()._wants_entry()
    assert not _s(today="2026-11-13")._wants_entry()       # signal day itself: not yet
    assert not _s(sym="AAPL")._wants_entry()                # not a pick


def test_a_name_sold_this_cycle_stays_out_until_the_next_decision():
    assert not _s(sold={"NVDA"})._wants_entry()


def test_no_entries_when_the_oms_cannot_be_read():
    assert not _s(sold=None)._wants_entry()


def test_no_entries_and_no_rebalance_exits_without_a_decision():
    bad = {"error": "no bar for the signal day", "picks": []}
    s = _s(dec=bad, sym="AAPL")
    assert not s._wants_entry()
    assert s._wants_exit([100.0] * 40, 39) == (False, None)


def test_a_holding_dropped_from_the_picks_exits_at_the_rebalance():
    assert _s(sym="AAPL")._wants_exit([100.0] * 40, 39) == (True, "rebalance")
    assert _s(sym="NVDA")._wants_exit([100.0] * 40, 39) == (False, None)


def test_trailing_stop_is_20pct_below_the_highest_close_since_entry():
    s = _s(sym="NVDA")
    s._entry_bar["NVDA"] = "2026-11-02"
    s._dates = ["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"]
    assert s._wants_exit([500.0, 100.0, 125.0, 101.0], 3) == (False, None)   # 101 > 0.8*125
    assert s._wants_exit([500.0, 100.0, 125.0, 99.0], 3) == (True, "trailing-stop")


def test_size_is_the_decisions_weight_of_capital():
    assert _s()._size(250.0) == 20           # 100k x 5% / 250


def test_the_ranking_is_the_decisions_order():
    assert _s()._ranked_today(None) == ["NVDA", "MU"]


def test_on_bar_exit_goes_through_the_chassis_broker_check(monkeypatch):
    s = _s(sym="AAPL")
    s.seed_positions({"AAPL": 10}, {})
    s._fill_price["AAPL"] = 200.0
    s._entry_bar["AAPL"] = "2026-10-16"
    monkeypatch.setattr(s, "_history", lambda sym, bar: ([200.0] * 40, None))
    monkeypatch.setattr(s, "_broker_positions", lambda: {"AAPL": 10.0})
    bar = Bar(symbol="AAPL", timestamp=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
              open=200, high=200, low=200, close=200, volume=1, timeframe_seconds=86400)
    orders = s.on_bar(bar)
    assert len(orders) == 1 and orders[0].side == OrderSide.SELL and orders[0].quantity == 10
    assert orders[0].strategy_id == "momentum_rotation_ibkr"


def test_on_bar_never_sells_what_the_broker_does_not_hold(monkeypatch):
    s = _s(sym="AAPL")
    s.seed_positions({"AAPL": 10}, {})
    s._fill_price["AAPL"] = 200.0
    s._entry_bar["AAPL"] = "2026-10-16"
    monkeypatch.setattr(s, "_history", lambda sym, bar: ([200.0] * 40, None))
    monkeypatch.setattr(s, "_broker_positions", lambda: {})
    bar = Bar(symbol="AAPL", timestamp=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
              open=200, high=200, low=200, close=200, volume=1, timeframe_seconds=86400)
    assert s.on_bar(bar) == []


def test_a_mid_cycle_start_waits_for_the_next_decision():
    assert _s(today="2026-11-20")._wants_entry()            # within the week
    assert not _s(today="2026-12-01")._wants_entry()        # stale list: wait
    # ...but a holding still exits on the rebalance or the stop
    assert _s(today="2026-12-01", sym="AAPL")._wants_exit([1.0] * 40, 39) == (True, "rebalance")
