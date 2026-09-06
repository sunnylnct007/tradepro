"""Enter 20-25 minutes after the open, not into the auction.

Owner, 6 Sep 2026: "the timing to enter on index is very important. we try to
enter 20-25 mins after market open for that exchange".

Both schedules ran at FIFTEEN minutes. Worse, a cron time is only a
convenience — a manual trigger or a Lambda retry could fire inside the opening
auction itself, where the spread is widest, the book thinnest, and the "session
open" the strikes are struck off has not settled.
"""
import datetime as _dt

import pytest

from tradepro_strategies.cli.index_strangle_paper import (
    ENTRY_MIN_MINUTES_AFTER_OPEN, MARKETS, _minutes_since_open,
)


def _at_ist(h, m):
    """A UTC instant for an India wall-clock time (IST = UTC+5:30)."""
    return _dt.datetime(2026, 9, 7, h, m, tzinfo=_dt.UTC) - _dt.timedelta(hours=5, minutes=30)


def _at_ny(h, m):
    """A UTC instant for a New York wall-clock time (EDT = UTC-4)."""
    return _dt.datetime(2026, 9, 7, h + 4, m, tzinfo=_dt.UTC)


def test_the_default_wait_is_inside_the_owners_window():
    assert 20 <= ENTRY_MIN_MINUTES_AFTER_OPEN <= 25


@pytest.mark.parametrize("hh,mm,expect", [(9, 15, 0), (9, 30, 15), (9, 40, 25)])
def test_minutes_since_open_india(hh, mm, expect):
    got = _minutes_since_open(MARKETS["NIFTY"], _at_ist(hh, mm))
    assert got is not None and round(got) == expect


@pytest.mark.parametrize("hh,mm,expect", [(9, 30, 0), (9, 45, 15), (9, 55, 25)])
def test_minutes_since_open_us(hh, mm, expect):
    got = _minutes_since_open(MARKETS["SPY"], _at_ny(hh, mm))
    assert got is not None and round(got) == expect


def test_before_the_open_there_is_no_elapsed_time():
    # Not zero — NONE. A pre-open run must not read as "0 minutes in" and then
    # be compared numerically against the wait.
    assert _minutes_since_open(MARKETS["SPY"], _at_ny(8, 0)) is None


def test_after_the_close_is_also_none():
    assert _minutes_since_open(MARKETS["SPY"], _at_ny(17, 0)) is None


def test_fifteen_minutes_in_is_INSIDE_the_window_we_refuse():
    # The exact case that was running daily on both schedules.
    assert _minutes_since_open(MARKETS["SPY"], _at_ny(9, 45)) < ENTRY_MIN_MINUTES_AFTER_OPEN
