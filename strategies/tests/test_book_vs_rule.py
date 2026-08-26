"""A price is never shown without its date, and a stale one is REFUSED.

26 Aug 2026. The first version of this tool printed SNDK at 1596.08 with no
date attached. That is the 21 AUGUST close — the store's last bar for the name
— while the stock was actually 1480.77, seven percent lower.

SNDK is stale because the nightly harvest scopes to the 244-name universe and
SNDK is not in it. Correct for the automated sleeve; exactly wrong for a view
of what you HOLD, because the names most likely to be missing from the harvest
are the ones outside the universe — which is the set this tool exists to
describe.

A price with no date is how Monday's fabricated TXN signal happened.

These tests exercise BEHAVIOUR, not source text. The first draft grepped the
module for phrases and failed on an f-string that happened to be split across
two lines — a test that breaks on reformatting while proving nothing.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from tradepro_strategies.cli import book_vs_rule as bvr


def _frame(n: int, last: dt.date, price: float = 100.0):
    """n daily bars ending EXACTLY on `last`, flat at `price`.

    Deliberately NOT bdate_range: it snaps a weekend end-date back to the
    previous Friday, so "three days ago" silently became five and the
    long-weekend test failed on a Wednesday while passing on a Monday. This
    repo has been bitten by weekend-shifted fixtures before; the age under test
    must be the age the test asked for.
    """
    idx = pd.date_range(end=pd.Timestamp(last, tz="UTC"), periods=n, freq="D")
    return pd.DataFrame(
        {"open": price, "high": price * 1.01, "low": price * 0.99,
         "close": price, "volume": 1_000_000}, index=idx)


@pytest.fixture
def store(monkeypatch):
    holdings: dict[str, pd.DataFrame] = {}
    monkeypatch.setattr(bvr, "_load", lambda s: holdings.get(s))
    monkeypatch.setattr(bvr, "universe_symbols", lambda: ["INUNI"])
    return holdings


def test_a_fresh_name_is_graded_and_carries_its_bar_date(store):
    store["INUNI"] = _frame(400, dt.date.today())
    r = bvr.assess("INUNI", {"INUNI"})
    assert r["state"] == "GRADED"
    assert r["stale"] is False
    assert r["bar"]                      # the date is always present
    assert r["in_universe"] is True


def test_a_stale_name_is_flagged_not_quietly_analysed(store):
    """SNDK's real shape: plenty of history, but the last bar is days old."""
    store["SNDK"] = _frame(400, dt.date.today() - dt.timedelta(days=5))
    r = bvr.assess("SNDK", set())
    assert r["stale"] is True
    assert r["age_days"] >= 5
    assert r["in_universe"] is False


@pytest.mark.parametrize("age,expected_stale", [(0, False), (3, False), (5, True), (12, True)])
def test_the_staleness_boundary(store, age, expected_stale):
    """Three days is a long weekend — a Friday close read on Monday — and must
    NOT be flagged; a check that fires every Monday is one people switch off.
    Beyond that the price is not current and the analysis is withheld."""
    store["X"] = _frame(400, dt.date.today() - dt.timedelta(days=age))
    r = bvr.assess("X", set())
    assert r["age_days"] == age
    assert r["stale"] is expected_stale


def test_a_name_too_short_to_grade_says_so_and_does_not_pretend(store):
    """SKHY has 30 sessions. 'No signal' and 'cannot be signalled' are
    different facts; reporting them identically is the failure this codebase
    keeps producing."""
    store["SKHY"] = _frame(30, dt.date.today())
    r = bvr.assess("SKHY", set())
    assert r["state"] == "CANNOT GRADE"
    assert "30 stored sessions" in r["note"]
    assert "sigma_below" not in r, "a name that cannot be graded must not be graded"


def test_a_name_with_no_bars_at_all_is_distinguished_from_a_short_one(store):
    r = bvr.assess("NOPE", set())
    assert r["state"] == "NO DATA"


def test_it_uses_the_one_rule_module_and_owns_no_copy():
    from tradepro_strategies.signals import mean_reversion as RULE
    assert bvr.entry_signal is RULE.entry_signal
    assert bvr.target_price is RULE.target_price
    assert bvr.SIGMA is RULE.SIGMA
    assert bvr.TREND_WINDOW is RULE.TREND_WINDOW
