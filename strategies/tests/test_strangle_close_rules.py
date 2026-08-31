"""Close on a profit target, or at end of day — whichever comes first.

Owner, 31 Aug 2026: "a auto close one on either profit or end of day", and the
purpose behind the shadow placements — "this way we can evaluate the execution".

THE TIME EXIT IS LOAD-BEARING, not a fallback. An external review made the
sharpest technical point anyone has made about this strategy: the strikes sit
~2.4 standard deviations away for ONE day but only ~0.92 across a week. Carried
overnight the geometry changes completely and NONE of the published evidence
describes the position any more. So it closes at the bell even at a loss.
"""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.cli import index_strangle_close as C
from tradepro_strategies.cli.index_strangle_paper import MARKETS


def _at(local: str, cfg: dict, day="2026-09-01"):
    from zoneinfo import ZoneInfo
    h, m = (int(x) for x in local.split(":"))
    return dt.datetime.fromisoformat(day).replace(
        hour=h, minute=m, tzinfo=ZoneInfo(cfg["tz"])).astimezone(dt.UTC)


SPY = MARKETS["SPY"]


def test_profit_target_closes_it():
    pos = {"credit": 100.0, "current_cost": 45.0}          # 55% decayed
    d = C.decide_close(pos, SPY, _at("12:00", SPY))
    assert d["close"] and d["trigger"] == "profit_target"


def test_short_of_target_holds_and_says_how_far():
    pos = {"credit": 100.0, "current_cost": 70.0}          # 30% decayed
    d = C.decide_close(pos, SPY, _at("12:00", SPY))
    assert not d["close"]
    assert "30%" in d["reason"] and "50%" in d["reason"]


def test_end_of_day_closes_EVEN_AT_A_LOSS():
    """The one that matters. Carrying a short strangle overnight is a trade
    nothing in this project has measured — so the bell wins over the P&L."""
    pos = {"credit": 100.0, "current_cost": 180.0}          # deeply underwater
    d = C.decide_close(pos, SPY, _at("15:50", SPY))
    assert d["close"] and d["trigger"] == "end_of_day"


def test_the_time_exit_outranks_the_profit_target():
    """At the bell it closes regardless, so the trigger must read end_of_day
    even when the target also happens to be met — otherwise the recorded
    reason misattributes why the position ended."""
    pos = {"credit": 100.0, "current_cost": 10.0}
    d = C.decide_close(pos, SPY, _at("15:50", SPY))
    assert d["trigger"] == "end_of_day"


def test_nothing_closes_outside_the_session():
    for t in ("08:00", "17:30"):
        d = C.decide_close({"credit": 100.0, "current_cost": 10.0}, SPY, _at(t, SPY))
        assert not d["close"] and "not open" in d["reason"]


def test_every_decision_carries_a_reason():
    """A close with no stated reason cannot be graded later — which is the
    entire point of recording these."""
    for cost in (10.0, 70.0, 180.0, None):
        for t in ("08:00", "12:00", "15:50"):
            d = C.decide_close({"credit": 100.0, "current_cost": cost}, SPY, _at(t, SPY))
            assert d.get("reason")


def test_the_target_is_not_quietly_tuned():
    """This project has twice set a parameter by judgement and retracted it.
    50% is the premium-sellers' convention and stays put until there is a
    measured sample to move it on."""
    assert C.TARGET_PCT == 0.50
