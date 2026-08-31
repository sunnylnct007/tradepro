"""The wheel board must name the CONTRACT, not just a strike and a duration.

Owner, 31 Aug 2026: "the option screen shd be telling me which period option i
have to place ... as there are diff periods".

THE GAP. Every eligible row carried `dte: 46` and no expiry field at all — no
`expiry`, no `expiration`, no `maturity`. A reader had to count 46 days forward
and then guess which LISTED expiry that lands on, and several are listed in any
given week. A screen that names a strike, a delta and a premium but not the
expiry has not named something you can place.

`chain_expiry` was already in scope in the same function — it is the expiry the
premium, delta and open interest were all read from, and it is used a few lines
earlier to match captured open interest exactly. It simply never reached the
payload. The SHORT tier had emitted "expiry" all along, so the two halves of one
screen disagreed about whether this mattered.

WEEKLY vs MONTHLY is a real distinction, not decoration. Standard monthlies
(third Friday) carry the deepest open interest and the tightest spreads;
weeklies decay faster and are thinner. The owner asked for the split explicitly
("u shd be able to split them in mnthly and weekly DTE").
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli.options_screen import _expiry_kind


@pytest.mark.parametrize("expiry", ["20260918", "2026-09-18", "20261016", "20260220"])
def test_third_friday_is_monthly(expiry):
    """The standard monthly — always the third Friday."""
    assert _expiry_kind(expiry) == "monthly", expiry


@pytest.mark.parametrize("expiry", ["20260904", "20260911", "20260925", "20261002"])
def test_other_fridays_are_weekly(expiry):
    assert _expiry_kind(expiry) == "weekly", expiry


def test_a_non_friday_is_weekly_not_monthly():
    """Daily/zero-DTE expiries land midweek. They are certainly not the standard
    monthly, and calling them so would point the reader at the wrong book."""
    assert _expiry_kind("20260916") == "weekly"      # a Wednesday


def test_the_first_friday_of_a_month_is_not_monthly():
    """The boundary that matters: day 15-21 is the third Friday. A first Friday
    falling on the 4th must not be graded monthly."""
    assert _expiry_kind("20260904") == "weekly"


def test_a_friday_on_the_21st_is_still_monthly():
    """Upper edge of the third-Friday window."""
    assert _expiry_kind("20260821") == "monthly"


@pytest.mark.parametrize("bad", [None, "", "garbage", "2026", "20261340"])
def test_unparseable_returns_none_rather_than_guessing(bad):
    """A wrong weekly/monthly label is worse than none — it would send the
    reader to a book that does not exist."""
    assert _expiry_kind(bad) is None


def test_the_screen_payload_carries_expiry_and_kind():
    """Structural: both fields must reach the row, beside dte."""
    import inspect

    from tradepro_strategies.cli import options_screen as OS
    src = inspect.getsource(OS)
    assert '"expiry": chain_expiry' in src, "the chosen expiry must reach the payload"
    assert '"expiry_kind": _expiry_kind(chain_expiry)' in src
