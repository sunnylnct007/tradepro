"""Market movers: our own bars, never a close from the wrong day, and a name
without today's bars is counted, not ranked."""
import ast

import numpy as np
import pandas as pd

from tradepro_strategies import market_movers as MM


def _m5(day: str, closes, vols=None, days_back=0):
    idx = pd.date_range(f"{day} 09:30", periods=len(closes), freq="5min", tz=MM.NY)
    return pd.DataFrame({"close": closes, "volume": vols or [100] * len(closes)}, index=idx)


def _daily(vals, end):
    return pd.Series([float(v) for v in vals],
                     index=pd.DatetimeIndex([d.date() for d in pd.bdate_range(end=end, periods=len(vals))]))


def test_the_move_is_against_the_previous_settled_close_not_todays():
    m5 = _m5("2026-11-17", [100.0, 104.0, 105.0])
    # the store already has a (partial) 17 Nov daily row: it must NOT be the base
    daily = _daily([90.0] * 70 + [100.0, 999.0], end="2026-11-17")
    r = MM.row_for("X", m5, daily)
    assert r["prev_close"] == 100.0 and r["prev_close_date"] == "2026-11-16"
    assert r["chg_pct"] == 5.0 and r["session"] == "2026-11-17"


def test_volume_is_to_the_same_time_of_day_over_prior_sessions():
    frames = [_m5(str(d.date()), [10.0] * 78, [100] * 78) for d in pd.bdate_range(end="2026-11-16", periods=20)]
    frames.append(_m5("2026-11-17", [10.0] * 6, [300] * 6))
    m5 = pd.concat(frames)
    assert MM.volume_x(m5, pd.Timestamp("2026-11-17").date(), "09:50") == 3.0


def test_a_name_behind_or_without_bars_is_named_not_ranked(monkeypatch):
    today = _m5("2026-11-17", [100.0, 101.0])
    old = _m5("2026-11-14", [100.0, 101.0])
    data = {"A": today, "B": old, "C": None}
    monkeypatch.setattr(MM, "_five_min", lambda s: data[s])
    monkeypatch.setattr(MM, "_daily", lambda s: _daily([100.0] * 70, end="2026-11-16"))
    art = MM.build(["A", "B", "C"], held={"A"})
    assert art["session"] == "2026-11-17" and art["ranked"] == 1
    assert art["behind"] == ["B"] and art["no_bars"] == ["C"]
    assert art["gainers"][0]["symbol"] == "A" and art["gainers"][0]["status"] == "held"


def test_the_module_cannot_place_orders():
    tree = ast.parse(open(MM.__file__).read())
    mods = {(n.module or "") for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
           {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(x in m for m in mods for x in ("router", "broker", "ibkr_gateway", "requests"))


def test_mixed_bar_times_are_shown_as_a_range(monkeypatch):
    data = {"A": _m5("2026-11-17", [100.0] * 3), "B": _m5("2026-11-17", [100.0] * 5)}
    monkeypatch.setattr(MM, "_five_min", lambda s: data[s])
    monkeypatch.setattr(MM, "_daily", lambda s: _daily([99.0] * 70, end="2026-11-16"))
    assert MM.build(["A", "B"])["bar_time_ny"] == "09:40–09:50"
