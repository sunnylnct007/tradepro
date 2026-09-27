"""A board holding Friday's signal on Sunday is working, not broken.

THE FALSE ALARM (27 Sep 2026, a Sunday). The desk check counted CALENDAR hours,
so boards whose producers run Mon-Fri read:

    [FAIL] Board · Watch / pre-earnings: 42h old (limit 30h)
    [FAIL] Board · Wheel (put selling):  32h old (limit 30h)

and the desk reported "BROKEN — do not trade from these boards" for boards that
held the newest data that exists. There is no Saturday signal to be missing.

A check that cries wolf every single weekend is one people learn to skip on
Monday, which is the day it matters. The frontend already counted market hours
and said why; the check that actually gates the banner did not.

This pins BOTH directions: a weekend must not age a board, and a genuinely
stale board must still fail.
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from tradepro_strategies.cli import desk_check


def _at(when: dt.datetime, iso: str) -> float | None:
    """Evaluate _hours_since as if 'now' were `when`."""
    real = dt.datetime

    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    with patch.object(desk_check.dt, "datetime", _Now):
        return desk_check._hours_since(iso)


FRI_1600 = dt.datetime(2026, 9, 25, 16, 0, tzinfo=dt.UTC)
SUN_1600 = dt.datetime(2026, 9, 27, 16, 0, tzinfo=dt.UTC)
MON_2200 = dt.datetime(2026, 9, 28, 22, 0, tzinfo=dt.UTC)
WED_1600 = dt.datetime(2026, 9, 23, 16, 0, tzinfo=dt.UTC)


def test_a_friday_board_is_not_stale_on_sunday():
    """48 calendar hours, but only Friday's remaining 8 are market hours."""
    age = _at(SUN_1600, FRI_1600.isoformat())
    assert age is not None
    assert age < 30, (
        f"Friday's board reads {age:.1f}h on Sunday — it would FAIL a 30h "
        "limit for holding the newest data that exists")
    assert abs(age - 8.0) < 0.5, f"expected ~8 market hours, got {age:.1f}"


def test_a_board_that_missed_its_own_monday_run_still_fails():
    """The guard must not become a hole. Wednesday -> Monday night is stale."""
    age = _at(MON_2200, WED_1600.isoformat())
    assert age is not None and age > 30, (
        f"a board last updated Wednesday reads {age:.1f}h on Monday night — "
        "that is genuinely stale and must still fail")


def test_weekend_time_contributes_nothing():
    """Saturday and Sunday add zero, whatever the span."""
    sat = dt.datetime(2026, 9, 26, 2, 0, tzinfo=dt.UTC)
    sun_late = dt.datetime(2026, 9, 27, 23, 0, tzinfo=dt.UTC)
    assert _at(sun_late, sat.isoformat()) == 0.0


def test_a_future_timestamp_is_not_negative_age():
    """Clock skew must read as fresh, never as a large negative number that
    would silently pass every limit."""
    assert _at(FRI_1600, SUN_1600.isoformat()) == 0.0


def test_missing_timestamp_stays_unknown():
    """Absence is not freshness — None must survive, not become 0."""
    assert _at(SUN_1600, None) is None
