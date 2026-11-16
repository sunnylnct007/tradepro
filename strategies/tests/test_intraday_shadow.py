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
