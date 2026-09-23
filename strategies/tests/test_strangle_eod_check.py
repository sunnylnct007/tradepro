"""Did the desk actually work today? — the post-close self-check.

Owner, 7 Sep 2026: "re we confident of our diagnostic and observablity we have
placed". No. Every failure in the first week was found by a human querying by
hand, days late:

    2 Sep  the job never ran                  found in CloudWatch, days later
    1 Sep  the close failed, 4 legs overnight found by checking positions
    4 Sep  placements 404'd for days          found as placed=None beside a P&L

/health/details covers deploy, api, worker and compare-cache — nothing about
whether the trading jobs ran. The capture got good this week; the ALERTING
never existed.

Every check below is a failure this desk has ACTUALLY had. A check nobody has
needed is noise, and noise is how the real signal dies.
"""
from tradepro_strategies.cli.index_strangle_eod import audit

MK = {
    "SPY":  {"paper_trade": True,  "tz": "America/New_York"},
    "QQQ":  {"paper_trade": True,  "tz": "America/New_York"},
    "NIFTY": {"paper_trade": False, "tz": "Asia/Kolkata"},
}
import datetime as _dt
TODAY = _dt.date.today().isoformat()


def _row(market, **kw):
    base = {"market": market, "exchange_date": TODAY, "expiry_kind": "monthly",
            "placed": None, "place_error": None, "credit_actual": None,
            "close_trigger": None, "realised_pnl": None}
    base.update(kw)
    return base


def test_no_rows_at_all_is_the_loudest_failure():
    # 2 Sep 2026. Nothing below can be trusted if the job never ran.
    r = audit([], MK)
    assert r["ok"] is False
    assert "did not run" in r["problems"][0]
    assert len(r["problems"]) == 1, "one clear cause, not a cascade"


def test_a_closed_exchange_is_ONE_line_not_one_per_market():
    # 7 Sep 2026 was US Labor Day. The first version reported five separate
    # problems for five markets that simply had no session — the cry-wolf shape
    # that makes a report unreadable.
    r = audit([_row("NIFTY")], MK)
    us = [p for p in r["problems"] if "America/New_York" in p]
    assert len(us) == 1
    assert "exchange was closed" in us[0]


def test_one_market_missing_while_its_peers_traded_IS_a_fault():
    r = audit([_row("SPY", placed=True, credit_actual=800.0,
                    close_trigger="end_of_day")], MK)
    assert any("QQQ" in p and "expected a row" in p for p in r["problems"])


def test_a_realised_pnl_with_no_placement_is_flagged():
    # 4 Sep 2026: placed=None beside +123.89 for days.
    r = audit([_row("SPY", realised_pnl=123.89), _row("QQQ", placed=False,
              place_error="could not resolve")], MK)
    assert any("record of opening it did not" in p for p in r["problems"])


def test_placed_but_never_closed_is_flagged():
    # 1 Sep 2026: four legs carried overnight on a malformed close request.
    r = audit([_row("SPY", placed=True, credit_actual=800.0),
               _row("QQQ", placed=False, place_error="margin")], MK)
    assert any("no close trigger" in p for p in r["problems"])


def test_placed_without_a_fill_price_is_flagged():
    r = audit([_row("SPY", placed=True, close_trigger="end_of_day"),
               _row("QQQ", placed=False, place_error="margin")], MK)
    assert any("credit_actual is NULL" in p for p in r["problems"])


def test_an_EXPECTED_refusal_is_not_a_problem():
    # The gate standing aside, or a market we deliberately park, is the desk
    # working. Flagging it would bury the real faults.
    r = audit([_row("SPY", placed=False, place_error="PARKED — deliberately off"),
               _row("QQQ", placed=False,
                    place_error="stand aside — vol gate above ceiling")], MK)
    assert r["ok"] is True, r["problems"] + r["operational"]
    assert r["placeable"] == 0   # neither was ever going to trade


def test_an_OPERATIONAL_refusal_is_NOT_a_clean_day():
    # THIS TEST PREVIOUSLY ASSERTED THE BUG.
    #
    # It fed exactly "could not resolve" and "margin" — the desk's two most
    # common real failures — and asserted ok is True, on the reasoning that a
    # refusal carrying a reason is the system working. So on 23 Sep 2026, when
    # the chain would not resolve for three of four units, the owner was
    # emailed "[STRANGLE OK] end-of-day check clean". A stated reason is not
    # the same as an acceptable outcome.
    r = audit([_row("SPY", placed=False,
                    place_error="could not resolve one or both contracts"),
               _row("QQQ", placed=False,
                    place_error="insufficient margin to fund the pair")], MK)
    assert r["ok"] is False
    assert r["problems"] == []          # the plumbing was fine...
    assert len(r["operational"]) == 2   # ...the desk still could not trade
    assert r["placed"] == 0 and r["placeable"] == 2


def test_WEEKLY_units_are_audited_too():
    # The loop filtered to expiry_kind == "monthly" and so audited half the
    # desk. Weeklies began placing 14 Sep 2026 and were never added; on 23 Sep
    # two weekly units failed to resolve a chain entirely unseen.
    r = audit([_row("SPY", placed=True, credit_actual=800.0,
                    close_trigger="end_of_day"),
               _row("SPY", expiry_kind="weekly", placed=False,
                    place_error="could not resolve one or both contracts"),
               _row("QQQ", placed=True, credit_actual=600.0,
                    close_trigger="end_of_day")], MK)
    assert r["ok"] is False, "a failed WEEKLY unit must not read as clean"
    assert any("SPY weekly" in o for o in r["operational"])
    assert r["placed"] == 2 and r["placeable"] == 3


def test_every_line_names_the_EXPIRY_not_just_the_market():
    # "SPX" cannot say whether the monthly or the weekly failed; both exist and
    # they fail independently.
    r = audit([_row("SPY", expiry_kind="weekly", placed=False,
                    place_error="could not resolve one or both contracts"),
               _row("QQQ", placed=True, credit_actual=1.0,
                    close_trigger="end_of_day")], MK)
    assert all(("monthly" in o or "weekly" in o) for o in r["operational"])


def test_a_clean_day_reports_clean():
    r = audit([_row("SPY", placed=True, credit_actual=800.0, close_trigger="end_of_day"),
               _row("QQQ", placed=True, credit_actual=600.0, close_trigger="profit_target")], MK)
    assert r["ok"] is True and r["problems"] == []
