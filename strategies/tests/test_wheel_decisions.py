"""The wheel decisions must be arguable, and must not bury the important one.

Built 25 Aug 2026 after reading the owner's live account: a hand-run wheel that
has realised roughly +$3,346 in seven weeks, with one position — APLD, assigned
at 38.61 and now 28.64 — quietly 26% under water while a covered call earning
$0.35 was being written against it. Nothing in the platform had ever said that
position needed a decision.
"""
from __future__ import annotations

import datetime as dt

from tradepro_strategies.wheel_decisions import review

TODAY = dt.date(2026, 8, 25)

APLD = [
    {"contract_description": "APLD", "position": 100, "market_price": 28.64,
     "average_price": 38.61, "asset_class": "STK"},
    {"contract_description": "APLD Sep18'26 38 CALL @AMEX", "position": -1,
     "market_price": 0.3524, "average_price": 1.0995, "asset_class": "OPT"},
]


def _by(book, sym, action):
    return [d for d in book.decisions if d.symbol == sym and d.action == action]


def test_the_position_that_cannot_be_repaired_is_surfaced():
    d = _by(review(APLD, TODAY), "APLD", "STOP-WRITING")
    assert d, "a 26% hole earning 1.6%/month must raise a decision"
    assert "17 MONTHS" in d[0].why or "MONTHS" in d[0].why


def test_it_outranks_the_take_profit_notice_on_the_same_contract():
    """The first version reported 'CLOSE — 68% profit' and never mentioned the
    hole. Both are true; only one is a decision about the strategy. A profit
    notice must never be what a stuck position looks like."""
    book = review(APLD, TODAY)
    apld = [d for d in book.decisions if d.symbol == "APLD"]
    assert len(apld) == 1
    assert apld[0].action == "STOP-WRITING"
    # ...and the profit is still mentioned, because it is how you act on it.
    assert "68%" in apld[0].why


def test_the_repair_is_priced_at_TODAY_premium_not_what_it_sold_for():
    """Priced off the 1.10 it sold for, APLD repairs in ~5 months and the rule
    stays silent. Priced off the 0.35 it is worth now, it is ~17 months. A
    decision about holding on must use the premium available from here."""
    book = review(APLD, TODAY)
    why = book.decisions[0].why
    assert "0.35 today" in why
    assert "1.6%" in why


def test_a_healthy_covered_call_is_left_alone():
    rows = [
        {"contract_description": "GOOGL", "position": 100, "market_price": 346.29,
         "average_price": 343.01, "asset_class": "STK"},
        {"contract_description": "GOOGL Sep18'26 360 CALL @AMEX", "position": -1,
         "market_price": 4.87, "average_price": 5.31, "asset_class": "OPT"},
    ]
    assert not _by(review(rows, TODAY), "GOOGL", "STOP-WRITING")


def test_take_profit_fires_at_half_the_premium():
    rows = [{"contract_description": "ACN Sep18'26 160 PUT @AMEX", "position": -1,
             "market_price": 1.03, "average_price": 2.99, "asset_class": "OPT"}]
    d = _by(review(rows, TODAY), "ACN", "CLOSE")
    assert d and "66%" in d[0].why


def test_long_options_are_ignored_because_the_wheel_is_short_premium():
    rows = [{"contract_description": "SPY Sep18'26 500 PUT @AMEX", "position": 1,
             "market_price": 1.0, "average_price": 5.0, "asset_class": "OPT"}]
    assert review(rows, TODAY).decisions == []


def test_every_decision_states_the_rule_it_came_from():
    """A recommendation without its reasoning is an instruction, and the point
    is that these can be argued with."""
    rows = APLD + [
        {"contract_description": "ACN Sep18'26 160 PUT @AMEX", "position": -1,
         "market_price": 1.03, "average_price": 2.99, "asset_class": "OPT"}]
    for d in review(rows, TODAY).decisions:
        assert d.rule and d.why
        if d.action != "HOLD":
            assert "rule" in d.rule
