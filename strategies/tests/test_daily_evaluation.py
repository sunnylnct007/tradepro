"""The daily evaluation: conditions as fixed in EVALUATION_CONDITIONS_V1, and
never a P&L from a close that predates the buy."""
from datetime import date

import pandas as pd
import pytest

from tradepro_strategies import daily_evaluation as E


def _series(vals, end="2026-11-16"):
    idx = pd.bdate_range(end=end, periods=len(vals))
    return pd.Series([float(v) for v in vals], index=idx)


def test_conditions_follow_the_v1_definitions(monkeypatch):
    spy = _series([100.0] * 220 + [110.0])                    # above its 200-day
    qqq = _series([100.0] * 220 + [98.0])                     # -2% day -> Down
    xlk = _series([100.0] * 55 + [96.0])                      # -4% over 5 sessions -> sell-off
    xle = _series([100.0] * 56)
    data = {"SPY": spy, "QQQ": qqq, "XLK": xlk, "XLE": xle}
    monkeypatch.setattr(E, "_closes", lambda sym, lookback_days=420: data.get(sym))
    c = E.conditions(date(2026, 11, 16))
    assert c["version"] == "EVALUATION_CONDITIONS_V1"
    assert c["market_trend"] == "up" and c["day_type"] == "Down"
    assert c["sectors_in_selloff"] == ["Information Technology"]
    assert "semis_selloff" not in c                           # SMH has no history: no tag, not "False"


def test_a_holding_bought_after_the_session_is_never_valued(monkeypatch):
    class R:
        def __init__(self, j): self._j = j
        def json(self): return self._j

    board = {"rows": [{"symbol": "MU", "action": "HOLD", "qty": 5}], "next_signal_date": "2026-12-15"}
    orders = [{"symbol": "MU_US_EQ", "side": "BUY", "state": "FILLED", "qty": 5, "avgFillPrice": 720.84,
               "createdAtUtc": "2026-11-16T14:34:29Z"}]
    acct = {"accounts": [{"broker": "IBKR_PAPER", "positions": [{"symbol": "MU", "qty": 5, "avgCost": 721.0}]}]}

    def get(url, **k):
        if "today-setups" in url:
            return R({"artifact": board})
        return R(orders if "/oms/" in url else acct)
    import requests
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(E, "_closes", lambda sym, lookback_days=420: _series([700.0, 778.31], end="2026-11-13"))
    import tradepro_strategies.momentum_rotation as M
    monkeypatch.setattr(M, "load_universe", lambda: (["MU"], {"MU": "Information Technology"}))
    r = E.rotation(date(2026, 11, 13), {"sectors_in_selloff": []}, "http://x", "t")
    assert r["holdings"] == [] and "after the evaluated session" in r["problems"][0]


def test_after_a_weekday_close_an_old_session_is_refused(monkeypatch):
    import datetime as dt

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 11, 16, 21, 40, tzinfo=dt.timezone.utc)

    class FakeDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2026, 11, 16)
    monkeypatch.setattr(E, "datetime", FakeDT)
    monkeypatch.setattr(E, "date", FakeDate)
    monkeypatch.setattr(E, "session_to_evaluate", lambda: dt.date(2026, 11, 13))
    with pytest.raises(E.NotSettled):
        E.build("http://x", "t")


def test_the_module_cannot_place_orders():
    import ast
    tree = ast.parse(open(E.__file__).read())
    names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(x in m for m in names | mods for x in ("router", "broker", "place", "ibkr_gateway"))
    src = open(E.__file__).read()
    assert "requests.post" not in src and ".post(" not in src


def test_benchmarks_start_at_the_open_of_the_first_buy_day():
    src = open(E.__file__).read()
    # the rule, stated where it is applied — and never the close before the buy
    assert "o_.loc[pd.Timestamp(first_buy)]" in src
    assert "before = c[c.index < pd.Timestamp(first_buy)]" not in src


def test_every_schedule_starts_from_the_live_books_decision_and_is_named(monkeypatch):
    import tradepro_strategies.momentum_rotation as M
    seen = []

    def bt(p, panels):
        seen.append((p.rebalance_every, p.start, p.end))
        nav = _series([1.0, 0.99, 0.985], end="2026-11-17")
        return {"nav": nav, "rebalances": [{"date": "2026-11-13", "picks": ["MU", "AMD"]}],
                "activity": {"trades": 10, "trailing_stop_exits": 0}}
    monkeypatch.setattr(M, "cached_panels", lambda s: None)
    monkeypatch.setattr(M, "backtest", bt)
    r = E.rotation_schedules(date(2026, 11, 17))
    assert [x[0] for x in seen] == ["monthly", "biweekly", "weekly"]
    assert all(x[1] == "2026-11-13" and x[2] == "2026-11-17" for x in seen)
    w = r["schedules"]["weekly"]
    assert w["since_start_pct"] == -1.5 and w["day_pct"] == -0.51 and w["latest_picks"] == ["MU", "AMD"]


def test_a_schedule_without_the_sessions_close_is_refused(monkeypatch):
    import tradepro_strategies.momentum_rotation as M
    monkeypatch.setattr(M, "cached_panels", lambda s: None)
    monkeypatch.setattr(M, "backtest", lambda p, panels: {
        "nav": _series([1.0, 0.99], end="2026-11-16"), "rebalances": [], "activity": {}})
    with pytest.raises(RuntimeError, match="no 2026-11-17 close"):
        E.rotation_schedules(date(2026, 11, 17))
