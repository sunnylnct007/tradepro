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


def test_entries_go_at_market_not_capped_at_the_signal_close(monkeypatch):
    """14 Nov: the chassis sent a DAY limit at signal close +1.5%. A momentum
    pick that opens 2% up would never fill; the backtest buys at the open."""
    import tradepro_strategies.paper.strategies.mean_reversion_swing as mrs
    from tradepro_strategies.paper.strategy import OrderType
    monkeypatch.setattr(mrs, "_entry_chase_pct", lambda: 1.5)
    s = _s(sym="NVDA")
    s.seed_positions({}, {})
    monkeypatch.setattr(s, "_history", lambda sym, bar: ([100.0] * 40, None))
    monkeypatch.setattr(s, "_broker_positions", lambda: {})
    monkeypatch.setattr(s, "remember", lambda *a, **k: None)
    bar = Bar(symbol="NVDA", timestamp=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
              open=100, high=100, low=100, close=100, volume=1, timeframe_seconds=86400)
    orders = s.on_bar(bar)
    assert len(orders) == 1 and orders[0].side == OrderSide.BUY
    assert orders[0].type == OrderType.MARKET and orders[0].limit_price is None
    assert orders[0].quantity == 50          # 5% of 100k at 100


def test_exits_are_left_as_they_are():
    from tradepro_strategies.paper.strategy import Order, OrderType
    sell = Order(strategy_id="x", symbol="A", side=OrderSide.SELL, quantity=1,
                 type=OrderType.MARKET, tag="t")
    assert MomentumRotationStrategy._at_market(sell) is sell


def test_a_free_slot_goes_to_the_best_name_not_already_bought(monkeypatch):
    """16 Nov: six of ten picks bought, four slots free, and rank[:4] was four
    names already owned, so the 5th-10th picks were refused with slots open."""
    picks = ["MRNA", "MU", "MPC", "VLO", "DELL", "PSX", "AMD", "TGT", "MTD", "MRK"]
    dec = dict(DEC, picks=picks, weight_each=0.0264)
    s = _s(sym="MRK", dec=dec)
    s.seed_positions({}, {})
    for x in ("MRNA", "MU", "MPC", "VLO", "PSX", "MTD"):
        s._entry_bar[x] = "2026-11-16"
    monkeypatch.setattr(s, "_history", lambda sym, bar: ([100.0] * 40, None))
    monkeypatch.setattr(s, "_broker_positions", lambda: {})
    monkeypatch.setattr(s, "remember", lambda *a, **k: None)
    bar = Bar(symbol="MRK", timestamp=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
              open=100, high=100, low=100, close=100, volume=1, timeframe_seconds=86400)
    orders = s.on_bar(bar)
    assert len(orders) == 1 and orders[0].side == OrderSide.BUY and orders[0].symbol == "MRK"


def test_a_full_sleeve_still_refuses(monkeypatch):
    picks = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]
    s = _s(sym="K", dec=dict(DEC, picks=picks))
    s.seed_positions({}, {})
    for x in picks[:10]:
        s._entry_bar[x] = "2026-11-16"
    monkeypatch.setattr(s, "_history", lambda sym, bar: ([100.0] * 40, None))
    monkeypatch.setattr(s, "_broker_positions", lambda: {})
    bar = Bar(symbol="K", timestamp=datetime(2026, 11, 16, 15, tzinfo=timezone.utc),
              open=100, high=100, low=100, close=100, volume=1, timeframe_seconds=86400)
    assert s.on_bar(bar) == []


def _held_board(monkeypatch, live):
    """MU bought 16 Nov at 720.84; the store's last close is 13 Nov (778.31),
    from BEFORE the buy — the exact state the board was in on 16 Nov."""
    import pandas as pd

    import tradepro_strategies.daily_bars as db
    import tradepro_strategies.live_quote as lq
    s = _s(sym="MU")
    s._orders_cache = []
    s._fill_price = {"MU": 720.84}
    s._entry_bar = {"MU": "2026-11-16"}
    s.seed_positions({"MU": 5}, {})
    monkeypatch.setattr(db, "load_bars", lambda sym, res, lookback_days=None: pd.DataFrame(
        {"close": [760.0, 778.31]}, index=pd.to_datetime(["2026-11-12", "2026-11-13"])))
    monkeypatch.setattr(lq, "live_prices", lambda syms: live)
    monkeypatch.setattr(s, "_board_decision", lambda: {**DEC, "picks": ["MU"]})
    b = s.board()
    return next(r for r in b["rows"] if r["symbol"] == "MU"), b["book"]


def test_pnl_is_against_the_live_price_not_a_close_from_before_the_buy(monkeypatch):
    row, book = _held_board(monkeypatch, {"MU": 724.47})
    assert row["pnl_pct"] == 0.5                 # was +8.0 against Friday's close
    assert row["pnl_usd"] == round(5 * (724.47 - 720.84), 2)
    assert row["now_source"] == "live"
    assert book["names_priced"] == 1 and book["pnl_usd"] == row["pnl_usd"] and book["up"] == 1


def test_no_live_price_and_no_close_since_the_buy_means_no_pnl(monkeypatch):
    row, book = _held_board(monkeypatch, {})
    assert "pnl_pct" not in row and row["pnl_note"] == "no price since the buy yet"
    assert book["names_priced"] == 0 and book["names_held"] == 1


def test_the_stop_is_set_on_the_day_of_the_buy(monkeypatch):
    row, _ = _held_board(monkeypatch, {})
    assert row["stop"] == round(720.84 * 0.8, 2)   # was blank: no close since entry
def test_rebalance_schedules_for_the_v2_study():
    import pandas as pd

    from tradepro_strategies import momentum_rotation as M
    d = pd.bdate_range("2026-01-01", "2026-03-31")
    assert len(M.rebalance_dates(d, M.with_overrides())) == 3          # default stays monthly
    assert len(M.rebalance_dates(d, M.with_overrides(rebalance_every="weekly"))) == 14
    assert len(M.rebalance_dates(d, M.with_overrides(rebalance_every="biweekly"))) == 7
    with pytest.raises(ValueError):
        M.rebalance_dates(d, M.with_overrides(rebalance_every="daily"))


def test_each_holding_carries_its_trend(monkeypatch):
    """Owner 16 Nov: the old positions table showed whether each stock's trend
    was green. 30 closes for the sparkline, the 30-session change, and the
    price against its 50-day average — from settled closes."""
    import pandas as pd

    import tradepro_strategies.daily_bars as db
    s = _s(sym="MU")
    s._orders_cache = []
    s._fill_price = {"MU": 100.0}
    s._entry_bar = {"MU": "2026-11-16"}
    s.seed_positions({"MU": 5}, {})
    closes = [float(x) for x in range(61, 121)]          # 60 rising closes, last 120
    idx = pd.bdate_range(end="2026-11-13", periods=60)
    monkeypatch.setattr(db, "load_bars", lambda sym, res, lookback_days=None: pd.DataFrame(
        {"close": closes}, index=idx))
    monkeypatch.setattr(s, "_board_decision", lambda: {**DEC, "picks": ["MU"]})
    row = next(r for r in s.board()["rows"] if r["symbol"] == "MU")
    assert len(row["spark"]) == 30 and row["spark"][-1] == 120.0
    assert row["trend_30d_pct"] == round((120 / 91 - 1) * 100, 1)
    assert row["vs_sma50_pct"] == round((120 / 95.5 - 1) * 100, 1)   # mean of 71..120
