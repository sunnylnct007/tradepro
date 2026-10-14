"""The desk places ONE unit until that unit is reliable.

Owner, 23 Sep 2026: "can we also disabke placing too many index option when we
are able to place only coupl;e successfully" / "firts we get one workig then we
can. get rest working".

It had been attempting four US units a session and landing about two. Over the
21 sessions to 23 Sep:

    XSP monthly   11 placed / 2 failed   85%
    XSP weekly     4 placed / 2 failed   67%
    SPX weekly     3 placed / 3 failed   50%
    SPX monthly    6 placed / 7 failed   46%  (3 of those BROKER rejections)

That table chose XSP MONTHLY originally. It was switched to XSP WEEKLY on
14 Oct 2026 because the monthly unit stopped being able to place at all — its
expiry rolled into a November chain XSP cannot resolve on this account. See
test_the_default_set_is_XSP_weekly_alone.

So the desk places ONE unit and nothing else. The rest stay EVALUATED and
recorded with a stated refusal, because a unit that is never attempted writes
no decision row, and the end-of-day check reads a missing row as a fault.
"""
import os
import pytest

from tradepro_strategies.cli.index_strangle_paper import (
    PLACE_UNITS, PLACE_EXPIRY_KINDS, _place_units, place_paper)
from tradepro_strategies.cli.index_strangle_eod import audit


@pytest.fixture(autouse=True)
def _no_env_override(monkeypatch):
    monkeypatch.delenv("TRADEPRO_STRANGLE_PLACE_UNITS", raising=False)


def _row(market, **kw):
    # Enough to reach the placement-set gate: a live, non-provisional candidate
    # in an open session. If the gate lets it past, the NEXT refusal will name
    # something else — which is exactly what the tests below assert on.
    base = {"market": market, "status": "CANDIDATE", "session_state": "open",
            "provisional": False, "legs": {k: {"put_strike": 100.0,
                                               "call_strike": 120.0, "dte": 21}
                                           for k in PLACE_EXPIRY_KINDS}}
    base.update(kw)
    return base


def test_the_default_set_is_XSP_weekly_alone():
    """PINNED ON PURPOSE. Which unit the desk places is a DECISION, and it must
    not drift without someone editing this line and saying why.

    Switched monthly -> weekly on 14 Oct 2026, and NOT as a widening: the
    monthly unit could not place at all. `_monthly_expiry(21)` rolled its target
    to 2026-11-20 on 5 Oct and XSP's November chain does not resolve on this
    account (verified with the market open: Oct resolves, Nov/Dec do not, while
    SPX November does). Eight sessions of "could not resolve one or both
    contracts".

    The gate study had independently found the monthly's edge to be a harness
    artifact (0.019% -> 0.001% of collateral once vol expansion is priced, vs
    weekly's 0.029% -> 0.021%), so both lines of evidence point the same way.
    """
    assert PLACE_UNITS == (("XSP", "weekly"),)
    assert _place_units() == (("XSP", "weekly"),)


def test_a_unit_outside_the_set_is_refused_without_touching_the_broker():
    # No network is mocked here on purpose: if the gate did not refuse FIRST,
    # this test would try to reach IBKR and fail loudly rather than pass.
    res = place_paper(_row("SPX"), kind="monthly")
    assert res["placed"] is False
    assert "not in the placement set" in res["reason"]
    assert res["expiry_kind"] == "monthly"


def test_the_refusal_NAMES_what_the_desk_is_placing():
    # A refusal that does not say what IS being traded cannot be acted on.
    # DERIVED from PLACE_UNITS rather than restated: this test is about the
    # refusal naming the live set, not about which set that is. Hardcoding it
    # meant a deliberate unit switch broke three tests, two of which were not
    # about the unit.
    live_market, live_kind = PLACE_UNITS[0]
    other = "monthly" if live_kind == "weekly" else "weekly"
    res = place_paper(_row(live_market), kind=other)
    assert f"{live_market} {live_kind}" in res["reason"], res["reason"]


def test_the_chosen_unit_is_NOT_refused_by_this_gate():
    res = place_paper(_row(PLACE_UNITS[0][0]), kind=PLACE_UNITS[0][1])
    # It may still refuse for its own reasons (no chain in a test process);
    # it must not refuse for THIS one.
    if res and res.get("placed") is False:
        assert "not in the placement set" not in (res.get("reason") or "")


def test_a_stood_down_unit_does_NOT_make_the_day_look_broken():
    # THE CROSS-MODULE ONE. The end-of-day check turns the subject red for any
    # refusal it does not recognise as expected. Standing three units down
    # deliberately must not produce "[STRANGLE COULD NOT TRADE]" every session
    # — that is the cry-wolf shape the 23 Sep fix existed to remove.
    mk = {"XSP": {"paper_trade": True, "tz": "America/New_York"},
          "SPX": {"paper_trade": True, "tz": "America/New_York"}}
    import datetime as _dt
    today = _dt.date.today().isoformat()
    stood_down = place_paper(_row("SPX"), kind="monthly")["reason"]
    rows = [{"market": "XSP", "expiry_kind": "monthly", "exchange_date": today,
             "placed": True, "credit_actual": 284.78, "close_trigger": "end_of_day"},
            {"market": "SPX", "expiry_kind": "monthly", "exchange_date": today,
             "placed": False, "place_error": stood_down}]
    res = audit(rows, mk)
    assert res["ok"] is True, res["problems"] + res["operational"]
    assert res["placed"] == 1 and res["placeable"] == 1


@pytest.mark.parametrize("raw,expected", [
    ("XSP:weekly",             (("XSP", "weekly"),)),
    ("XSP:monthly,SPX:weekly", (("XSP", "monthly"), ("SPX", "weekly"))),
    ("  xsp : MONTHLY ",       (("XSP", "monthly"),)),
    ("all",                    None),
    ("*",                      None),
])
def test_the_set_is_configurable_without_a_deploy(monkeypatch, raw, expected):
    monkeypatch.setenv("TRADEPRO_STRANGLE_PLACE_UNITS", raw)
    assert _place_units() == expected


@pytest.mark.parametrize("raw", ["XSP", "XSP:daily", "XSP:monthly,SPX:yearly", ":monthly"])
def test_a_TYPO_falls_back_to_the_default_and_never_widens(monkeypatch, raw):
    # A malformed override must not be the reason a unit did or did not trade.
    # Silently widening would place four units again; silently narrowing to
    # nothing would stop the desk. Both are worse than ignoring the value.
    monkeypatch.setenv("TRADEPRO_STRANGLE_PLACE_UNITS", raw)
    assert _place_units() == PLACE_UNITS
