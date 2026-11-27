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
    monkeypatch.setattr(s, "_working_sells", lambda: {})   # no SELL working at the OMS (25 Nov guard)
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
    monkeypatch.setattr(M, "decide", lambda d, p=None: nov)
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
    # 31 Mar 2026 is a Tuesday: that week is still in progress, so it is not a week-end
    assert len(M.rebalance_dates(d, M.with_overrides(rebalance_every="weekly"))) == 13
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


def test_the_board_carries_market_and_sector_gauges(monkeypatch):
    import pandas as pd

    import tradepro_strategies.daily_bars as db
    import tradepro_strategies.universe as U
    s = _s()
    s._orders_cache = []
    monkeypatch.setattr(U, "MARKET_GAUGES", ("QQQ", "SMH"))
    idx = pd.bdate_range(end="2026-11-13", periods=260)

    def bars(sym, res, lookback_days=None):
        if sym == "SMH" or res == "5m":
            return None
        return pd.DataFrame({"close": [100.0] * 259 + [90.0]}, index=idx)
    monkeypatch.setattr(db, "load_bars", bars)
    monkeypatch.setattr(s, "_board_decision", lambda: DEC)
    g = {x["symbol"]: x for x in s.board()["gauges"]}
    assert g["QQQ"]["vs_sma200_pct"] < 0 and g["QQQ"]["from_high_pct"] == -10.0
    assert g["SMH"]["error"] == "no daily bars yet"


def test_market_gauges_are_harvested():
    from tradepro_strategies.universe import MARKET_GAUGES, harvest_symbols
    assert "SMH" in MARKET_GAUGES and set(MARKET_GAUGES) <= set(harvest_symbols())


def _stop_bar_run(monkeypatch, closes, dates, held=10.0):
    """The trailing stop, end to end through on_bar: the chassis's own history
    read sets the session dates the stop measures from, the broker is asked
    first, and the order that comes out is what the router would receive."""
    s = _s(sym="NVDA")                    # a current pick: only the stop can sell it
    s.seed_positions({"NVDA": 10}, {})
    s._fill_price["NVDA"] = 100.0
    s._entry_bar["NVDA"] = "2026-11-16"

    def hist(sym, bar):
        s._dates = list(dates)
        return list(closes), None
    monkeypatch.setattr(s, "_history", hist)
    monkeypatch.setattr(s, "_broker_positions", lambda: {"NVDA": held})
    monkeypatch.setattr(s, "_working_sells", lambda: {})   # no SELL working at the OMS (25 Nov guard)
    s._today = "2026-12-02"
    s._dec_cache["2026-12-02"] = DEC
    bar = Bar(symbol="NVDA", timestamp=datetime(2026, 12, 2, 15, tzinfo=timezone.utc),
              open=90, high=90, low=90, close=90, volume=1, timeframe_seconds=86400)
    return s.on_bar(bar)


def test_a_close_20pct_below_the_peak_since_entry_sells_the_whole_holding_at_market(monkeypatch):
    from tradepro_strategies.paper.strategy import OrderType
    # 500 before the buy must never count as the peak; since the buy the peak
    # is 130, so the stop is 104 and a 103 close sells.
    closes = [500.0] * 35 + [100.0, 130.0, 110.0, 103.0]
    dates = ["2026-10-01"] * 35 + ["2026-11-16", "2026-11-20", "2026-11-27", "2026-12-01"]
    orders = _stop_bar_run(monkeypatch, closes, dates)
    assert len(orders) == 1
    o = orders[0]
    assert o.side == OrderSide.SELL and o.type == OrderType.MARKET and o.quantity == 10
    assert o.strategy_id == "momentum_rotation_ibkr"


def test_a_close_just_above_the_stop_holds(monkeypatch):
    closes = [500.0] * 35 + [100.0, 130.0, 110.0, 105.0]  # 105 > 104
    dates = ["2026-10-01"] * 35 + ["2026-11-16", "2026-11-20", "2026-11-27", "2026-12-01"]
    assert _stop_bar_run(monkeypatch, closes, dates) == []


def test_a_stop_never_sells_more_than_the_broker_holds(monkeypatch):
    closes = [500.0] * 35 + [100.0, 130.0, 110.0, 103.0]
    dates = ["2026-10-01"] * 35 + ["2026-11-16", "2026-11-20", "2026-11-27", "2026-12-01"]
    assert _stop_bar_run(monkeypatch, closes, dates, held=0.0) == []

def test_a_week_in_progress_is_not_a_rebalance():
    """17 Nov 2026: with data to a Monday, that Monday was read as the end of
    its week, a weekly rebalance that vanished on Tuesday's run."""
    import pandas as pd

    from tradepro_strategies import momentum_rotation as M
    p = M.with_overrides(rebalance_every="weekly")
    to_mon = M.rebalance_dates(pd.bdate_range("2026-11-02", "2026-11-16"), p)
    assert [str(x.date()) for x in to_mon] == ["2026-11-06", "2026-11-13"]
    to_fri = M.rebalance_dates(pd.bdate_range("2026-11-02", "2026-11-20"), p)
    assert str(to_fri[-1].date()) == "2026-11-20"
    # a holiday-short week counts once the next week has begun
    short = pd.DatetimeIndex([x for x in pd.bdate_range("2026-11-23", "2026-11-30") if x.day != 27])
    assert "2026-11-26" in [str(x.date()) for x in M.rebalance_dates(short, p)]


# ── a second book on another schedule (owner 17 Nov: "capture many executions") ──
def _weekly(today, sym="NVDA"):
    wk = {**DEC, "next_signal_date": "2026-11-20"}
    s = MomentumRotationStrategy(strategy_id="momentum_rotation_weekly_t212",
                                 params={"capital": 90_000, "rebalance_every": "weekly"})
    s._today, s._cur = today, sym
    s._dec_cache[today] = wk
    s._orders_cache = []
    return s


def test_the_weekly_book_publishes_its_own_board_and_the_monthly_keeps_its_name():
    assert _s().board_name == "momentum_rotation"
    assert _weekly("2026-11-16").board_name == "momentum_rotation_weekly"


def test_a_weekly_book_started_mid_week_waits_for_the_next_decision():
    assert _weekly("2026-11-16")._wants_entry()              # the order day
    assert _weekly("2026-11-17")._wants_entry()              # one retry day
    assert not _weekly("2026-11-18")._wants_entry()          # last week's list: wait
    assert _s(today="2026-11-19")._wants_entry()             # monthly window unchanged


def test_the_weekly_book_asks_for_the_weekly_decision(monkeypatch):
    import tradepro_strategies.momentum_rotation as M
    seen = []
    monkeypatch.setattr(M, "decide", lambda d, p=None: seen.append(p.rebalance_every) or {**DEC})
    s = MomentumRotationStrategy(strategy_id="momentum_rotation_weekly_t212",
                                 params={"capital": 90_000, "rebalance_every": "weekly"})
    s._today = "2026-11-23"
    s._decision()
    assert seen == ["weekly"]


def test_the_calendar_decides_weekly_and_every_second_week_from_the_anchor():
    from datetime import date

    from tradepro_strategies.momentum_rotation import signal_dates_around as S
    w = S(date(2026, 11, 23), "weekly")
    assert (str(w["signal_date"]), str(w["order_date"]), str(w["next_signal_date"])) == \
        ("2026-11-20", "2026-11-23", "2026-11-27")
    b = S(date(2026, 11, 23), "biweekly")
    assert (str(b["signal_date"]), str(b["next_signal_date"])) == ("2026-11-13", "2026-11-27")
    m = S(date(2026, 11, 23))
    assert (str(m["signal_date"]), str(m["next_signal_date"])) == ("2026-11-13", "2026-12-15")
    with pytest.raises(ValueError):
        S(date(2026, 11, 23), "daily")


def test_the_lane_passes_its_schedule_to_the_strategy():
    from tradepro_strategies.cli.paper_session import _build_strategy, _parse_args
    argv = ["--broker", "t212", "--strategy", "momentum_rotation",
            "--strategy-id", "momentum_rotation_weekly_t212", "--capital-usd", "90000"]
    weekly = _build_strategy(_parse_args(argv + ["--rebalance-every", "weekly"]), ["MU"])
    monthly = _build_strategy(_parse_args(argv), ["MU"])
    assert weekly._every() == "weekly" and weekly.board_name == "momentum_rotation_weekly"
    assert monthly._every() == "monthly" and monthly.board_name == "momentum_rotation"


def test_the_board_shows_the_working_exit_not_the_newest_dead_duplicate():
    """25 Nov 2026: SUBMITTED at 21:22, a duplicate REJECTED at 21:38 — the
    board said "paper: rejected" beside a sell working at IBKR."""
    from tradepro_strategies.paper.strategies.momentum_rotation import pick_order_to_show
    sub = {"state": "SUBMITTED", "createdAtUtc": "2026-11-25T21:22:23Z", "brokerOrderId": "125427032"}
    dup = {"state": "REJECTED", "createdAtUtc": "2026-11-25T21:38:43Z", "brokerOrderId": None}
    assert pick_order_to_show([sub, dup]) is sub
    assert pick_order_to_show([dup]) is dup            # nothing alive: show what there is
    assert pick_order_to_show([]) is None
    filled = {"state": "FILLED", "createdAtUtc": "2026-11-27T14:30:05Z"}
    assert pick_order_to_show([sub, dup, filled]) is filled


def test_the_market_line_says_what_today_says_not_only_the_decision_day():
    from tradepro_strategies.paper.strategies.momentum_rotation import market_line
    dec = {"market_ok": True, "next_signal_date": "2026-12-15"}
    assert market_line(dec, None).endswith("invested")
    assert market_line(dec, {"market_ok": True, "as_of": "2026-11-27"}).endswith("invested")
    line = market_line(dec, {"market_ok": False, "as_of": "2026-11-27"})
    assert "BELOW its 200-day" in line and "2026-12-15" in line and "no exit before then" in line
    back = market_line({"market_ok": False, "next_signal_date": "2026-12-15"}, {"market_ok": True, "as_of": "2026-12-01"})
    assert "back ABOVE" in back and "would invest" in back
