"""The intraday rules' forward record: same frozen rules as the backtests, no
orders, and a session counts only once it is over."""
from datetime import date

import pandas as pd

from tradepro_strategies import intraday_shadow as S


def _session(d: str, closes: list[float], vol0: float = 1000, end: str = "15:55",
             first: tuple[float, float, float, float] | None = None) -> tuple[date, pd.DataFrame]:
    idx = pd.date_range(f"{d} 09:30", f"{d} {end}", freq="5min", tz="America/New_York")
    n = len(idx)
    c = (closes + [closes[-1]] * n)[:n]
    df = pd.DataFrame({"open": c, "high": [x + 0.1 for x in c], "low": [x - 0.1 for x in c],
                       "close": c, "volume": [vol0] + [1000] * (n - 1)}, index=idx)
    if first:
        df.iloc[0, :4] = first
    return date.fromisoformat(d), df


def _history(n=20):
    days = pd.bdate_range("2026-10-01", periods=n)
    return [_session(str(d.date()), [100.0]) for d in days]


def test_rule7_long_breakout_then_flat_at_the_close():
    hist = _history()
    # 09:30 candle up on 3x volume, high 101; a later bar closes 101.5 -> long
    d, g = _session("2026-11-16", [100.5, 101.5, 102.0, 103.0], vol0=3000, first=(100.0, 101.0, 99.5, 100.8))
    rows = S.rule7_trades("X", hist + [(d, g)], date(2026, 11, 16))
    assert len(rows) == 1 and rows[0]["side"] == "long" and rows[0]["entry"] == 101.5
    assert rows[0]["exit_why"] == "close" and rows[0]["exit"] == 103.0


def test_rule7_needs_twice_the_usual_first_candle_volume():
    hist = _history()
    d, g = _session("2026-11-16", [100.5, 101.5], vol0=1500, first=(100.0, 101.0, 99.5, 100.8))
    assert S.rule7_trades("X", hist + [(d, g)], date(2026, 11, 16)) == []


def test_rule7_stop_at_the_other_end_of_the_first_candle():
    hist = _history()
    d, g = _session("2026-11-16", [100.5, 101.5, 99.0], vol0=3000, first=(100.0, 101.0, 99.5, 100.8))
    r = S.rule7_trades("X", hist + [(d, g)], date(2026, 11, 16))[0]
    # the bar OPENED at 99.0, through the 99.5 stop: it exits at its open
    assert r["exit_why"] == "stop" and r["exit"] == 99.0


def test_rule2_gap_down_break_short_hits_target():
    prev = _session("2026-11-13", [100.0])
    # opens 97 (-3%), 09:30-09:55 low ~96.9; 10:00 bar closes 96.0 below the range and VWAP
    closes = [97.0] * 6 + [96.0] + [95.0, 94.0]
    d, g = _session("2026-11-16", closes)
    q_prev = _session("2026-11-13", [500.0])
    qd, qg = _session("2026-11-16", [499.0] * 6 + [495.0] * 80)
    qqq = {qd: (qg, S._vwap(qg))}
    sig = S.rule2_signals("X", [prev, (d, g)], qqq, date(2026, 11, 16))
    assert len(sig) == 1 and sig[0]["entry"] == 96.0
    r = S.rule2_trades(sig)[0]
    assert r["exit_why"] == "target" and r["pct"] > 0


def test_an_unfinished_session_is_in_progress_not_a_result(monkeypatch):
    hist = _history()
    d, g = _session("2026-11-16", [100.5, 101.5, 102.0], vol0=3000, first=(100.0, 101.0, 99.5, 100.8), end="14:00")
    qhist = [_session(str(x.date()), [500.0]) for x in pd.bdate_range("2026-10-01", periods=20)]
    q_today = _session("2026-11-16", [500.0], end="14:00")
    data = {"QQQ": qhist + [q_today], "X": hist + [(d, g)]}
    monkeypatch.setattr(S, "_sessions", lambda sym, lb: data[sym])
    a = S.build(start="2026-11-16", symbols=["X"])
    assert a["sessions"] == 0 and a["latest_session_final"] is False
    assert a["rules"]["rule7"]["record"] == {"trades": 0}
    assert a["trades"][0]["exit_why"].startswith("in progress")
    assert a["placed_orders"] == 0


def test_a_name_whose_bars_stopped_is_named(monkeypatch):
    qs = [_session(str(x.date()), [500.0]) for x in pd.bdate_range("2026-10-01", periods=25)]
    data = {"QQQ": qs, "X": qs[:-3]}
    monkeypatch.setattr(S, "_sessions", lambda sym, lb: data[sym])
    a = S.build(start="2026-11-01", symbols=["X"])
    assert a["stale"] and a["stale"][0].startswith("X")


def test_the_module_cannot_place_orders():
    import ast
    tree = ast.parse(open(S.__file__).read())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names} | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(x in m for m in imported for x in ("requests", "router", "oms", "broker", "ibkr", "t212"))


def test_rule7_records_breakout_bar_volume_against_the_same_time_of_day():
    hist = _history()                     # every bar 1000 shares
    d, g = _session("2026-11-16", [100.5, 101.5, 102.0], vol0=3000, first=(100.0, 101.0, 99.5, 100.8))
    g.iloc[1, g.columns.get_loc("volume")] = 2000       # the 09:35 breakout bar: 2x usual
    r = S.rule7_trades("X", hist + [(d, g)], date(2026, 11, 16))[0]
    assert r["entry_time"] == "09:35" and r["breakout_volume_x"] == 2.0 and r["breakout_volume"] == "high"


def test_a_trade_that_hit_its_target_is_settled_before_the_close(monkeypatch):
    prev = _session("2026-11-13", [100.0])
    d, g = _session("2026-11-16", [97.0] * 6 + [96.0, 95.0, 94.0], end="12:00")
    qhist = _session("2026-11-13", [500.0])
    qd, qg = _session("2026-11-16", [499.0] * 6 + [495.0] * 80, end="12:00")
    data = {"QQQ": [qhist, (qd, qg)], "X": [prev, (d, g)]}
    monkeypatch.setattr(S, "_sessions", lambda sym, lb: data[sym])
    a = S.build(start="2026-11-16", symbols=["X"])
    t = a["trades"][0]
    assert t["exit_why"] == "target" and t["final"] is True
    assert a["rules"]["rule2"]["record"]["trades"] == 1


# ── INTRADAY_SHORT_BLACKOUT_V1 ───────────────────────────────────────────────

def test_the_session_before_thanksgiving_and_a_friday_are_breaks_a_tuesday_is_not():
    assert S.into_break(date(2026, 11, 25))        # Thu 26 Nov 2026 is Thanksgiving
    assert S.into_break(date(2026, 11, 20))        # Friday
    assert not S.into_break(date(2026, 11, 24))    # Tuesday before a normal Wednesday
    assert not S.into_break(date(2026, 11, 26))    # a holiday is not a session at all


def _short_row(d: str, prior_closes: list[float]) -> tuple[dict, list]:
    days = ["2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25"]
    ss = []
    for day, px in zip(days, prior_closes + [prior_closes[-1]]):
        ss.append(_session(day, [px]))
    row = {"rule": "rule2", "date": d, "symbol": "X", "side": "short"}
    return row, ss


def test_a_short_after_a_5pct_down_day_into_a_break_is_tagged_blackout():
    row, ss = _short_row("2026-11-25", [100.0, 100.0, 92.0])   # 24 Nov closed -8%
    S.tag_blackout(row, ss)
    assert row["blackout"] is True and row["prior_day_pct"] == -8.0 and row["into_break"] is True
    assert "INTRADAY_SHORT_BLACKOUT_V1" in row["blackout_why"]


def test_a_small_down_day_or_a_normal_session_is_not_blacked_out():
    row, ss = _short_row("2026-11-25", [100.0, 100.0, 98.0])   # -2%: not enough
    S.tag_blackout(row, ss)
    assert row["blackout"] is False
    row, ss = _short_row("2026-11-24", [100.0, 92.0, 92.0])    # -8% but Tuesday
    S.tag_blackout(row, ss)
    assert row["blackout"] is False


def test_a_long_is_never_blacked_out_and_the_rules_themselves_are_unchanged():
    row, ss = _short_row("2026-11-25", [100.0, 100.0, 92.0])
    row["side"] = "long"
    S.tag_blackout(row, ss)
    assert row["blackout"] is False
    assert S.R2_GAP == -0.02 and S.R2_STOP == 0.015 and S.R7_VOLUME_X == 2.0


def test_the_record_shows_the_shorts_split_by_blackout(monkeypatch):
    """A rule-2 short on 25 Nov after an -8% day: counted in the rule's record
    (pre-registered, unchanged) AND shown under blackout_blocked."""
    hist_days = [str(d.date()) for d in pd.bdate_range("2026-10-26", "2026-11-24")]
    x_hist = [_session(d, [100.0]) for d in hist_days[:-1]] + [_session(hist_days[-1], [92.0])]
    # 25 Nov: gap down 3%, break of the opening range at 10:00 while below VWAP
    closes = [89.0, 89.0, 89.0, 89.0, 89.0, 89.0, 88.0] + [86.0] * 100
    d, g = _session("2026-11-25", closes)
    g.iloc[0, 0] = 89.0
    q_hist = [_session(dd, [500.0]) for dd in hist_days]
    qc = [500.0] * 6 + [495.0] + [490.0] * 100
    q_today = _session("2026-11-25", qc)
    data = {"QQQ": q_hist + [q_today], "X": x_hist + [(d, g)]}
    monkeypatch.setattr(S, "_sessions", lambda sym, lb: data[sym])
    a = S.build(start="2026-11-16", symbols=["X"])
    r2 = a["rules"]["rule2"]
    assert r2["record"]["trades"] == 1
    assert r2["shorts"]["blackout_blocked"]["trades"] == 1
    assert r2["shorts"]["allowed"] == {"trades": 0}
    assert a["trades"][0]["blackout"] is True


def test_a_half_day_session_is_complete_at_its_own_close():
    assert S.last_bar_start(date(2026, 11, 27)) == "12:55"   # day after Thanksgiving
    assert S.last_bar_start(date(2026, 11, 24)) == "15:55"
    assert S.last_bar_start(date(2026, 11, 26)) == "15:55"   # holiday: not a session, default


def test_a_half_day_record_is_final_when_its_last_bar_is_1255(monkeypatch):
    hist_days = [str(d.date()) for d in pd.bdate_range("2026-10-26", "2026-11-25")]
    x_hist = [_session(d, [100.0]) for d in hist_days]
    d, g = _session("2026-11-27", [100.5, 101.5, 102.0], vol0=3000, first=(100.0, 101.0, 99.5, 100.8), end="12:55")
    q_hist = [_session(dd, [500.0]) for dd in hist_days]
    q_today = _session("2026-11-27", [500.0], end="12:55")
    data = {"QQQ": q_hist + [q_today], "X": x_hist + [(d, g)]}
    monkeypatch.setattr(S, "_sessions", lambda sym, lb: data[sym])
    a = S.build(start="2026-11-16", symbols=["X"])
    assert a["latest_session"] == "2026-11-27" and a["latest_session_final"] is True
