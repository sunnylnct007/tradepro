"""The momentum rotation's paper sleeve trades the shared decision through
swing's chassis (MOMENTUM_ROTATION_GATES_V1.md, spec 12 Nov 2026)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tradepro_strategies.paper.strategies.momentum_rotation import MomentumRotationStrategy
from tradepro_strategies.paper.strategy import Bar, OrderSide

@pytest.fixture(autouse=True)
def _no_market_data(monkeypatch):
    """The board's daily ranking and live prices read the store and Yahoo;
    unit tests must not."""
    import tradepro_strategies.live_quote as lq
    import tradepro_strategies.momentum_rotation as mr
    monkeypatch.setattr(mr, "ranking_today", lambda *a, **k: {
        "date": "2026-11-13", "picks": ["NVDA", "AMD"], "scores": {}, "market_ok": True})
    monkeypatch.setattr(lq, "live_prices", lambda syms: {})


DEC = {"signal_date": "2026-11-13", "order_date": "2026-11-16",
       "next_signal_date": "2026-12-15", "picks": ["NVDA", "MU"],
       "scores": {"NVDA": 0.9, "MU": 0.7}, "weight_each": 0.05,
       "allocation": 0.5, "portfolio_vol": 0.3, "reason": "2 picks"}


def _s(today="2026-11-16", sym="NVDA", dec=DEC, sold=frozenset()):
    s = MomentumRotationStrategy(strategy_id="momentum_rotation_ibkr",
                                 params={"capital": 100_000})
    s._today, s._cur = today, sym
    s._dec_cache[today] = dec
    s._orders_cache = None if sold is None else [
        {"strategyId": "momentum_rotation_ibkr", "symbol": x, "side": "SELL",
         "state": "FILLED", "createdAtUtc": "2026-11-17T15:00:00Z"} for x in sold]
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


def test_the_board_says_buy_sell_hold_and_what_paper_did(monkeypatch):
    import pandas as pd

    import tradepro_strategies.daily_bars as db
    s = _s(sym="NVDA")
    s._orders_cache = [{"strategyId": "momentum_rotation_ibkr", "symbol": "NVDA", "side": "BUY",
                        "state": "FILLED", "avgFillPrice": 100.0, "createdAtUtc": "2026-11-16T14:31:00Z"}]
    s._fill_price = {"AAPL": 200.0}
    s._entry_bar = {"AAPL": "2026-10-16"}
    idx = pd.to_datetime(["2026-10-16", "2026-11-13"])
    monkeypatch.setattr(db, "load_bars", lambda sym, res, lookback_days=None: pd.DataFrame(
        {"close": [220.0, 210.0]}, index=idx))
    monkeypatch.setattr(s, "_board_decision", lambda: DEC)
    b = s.board()
    acts = {(r["action"], r["symbol"]): r for r in b["rows"]}
    assert ("BUY", "NVDA") in acts and acts[("BUY", "NVDA")]["paper"].startswith("filled")
    assert ("BUY", "MU") in acts
    assert ("SELL", "AAPL") in acts                      # held, not in the picks
    assert acts[("SELL", "AAPL")]["stop"] == 176.0      # 220 peak x 0.8


def test_the_board_shows_the_next_decision_once_its_close_is_in(monkeypatch):
    import tradepro_strategies.momentum_rotation as M
    s = _s(today="2026-11-13", dec={**DEC, "signal_date": "2026-10-15", "order_date": "2026-10-16",
                                     "picks": ["OLD"]})
    nov = {**DEC, "picks": ["NEW"]}
    monkeypatch.setattr(M, "decide", lambda d: nov)
    assert s._board_decision()["picks"] == ["NEW"]
    s._orders_cache = []
    monkeypatch.setattr(s, "_board_decision", lambda: nov)
    row = s.board()["rows"][0]
    assert row["symbol"] == "NEW" and row["paper"] == "to buy at the 2026-11-16 open"


def test_a_pick_past_its_entry_week_says_so():
    s = _s(today="2026-12-01")
    s._orders_cache = []
    import types
    s._board_decision = types.MethodType(lambda self: DEC, s)
    paper = {r["symbol"]: r["paper"] for r in s.board()["rows"]}
    assert paper["NVDA"].startswith("entry window closed")


def test_the_board_shows_todays_ranking_and_what_is_drifting(monkeypatch):
    s = _s()
    s._orders_cache = []
    monkeypatch.setattr(s, "_board_decision", lambda: DEC)
    t = s.board()["today"]
    assert t["entering"] == ["AMD"] and t["leaving"] == ["MU"]
