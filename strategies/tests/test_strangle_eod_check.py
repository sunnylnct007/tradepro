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


def test_a_refusal_WITH_a_reason_is_not_a_problem():
    # The gate refusing, or a market that cannot be funded, is the system
    # working. Flagging it would bury the real faults.
    r = audit([_row("SPY", placed=False, place_error="could not resolve"),
               _row("QQQ", placed=False, place_error="margin")], MK)
    assert r["ok"] is True, r["problems"]


def test_a_clean_day_reports_clean():
    r = audit([_row("SPY", placed=True, credit_actual=800.0, close_trigger="end_of_day"),
               _row("QQQ", placed=True, credit_actual=600.0, close_trigger="profit_target")], MK)
    assert r["ok"] is True and r["problems"] == []
