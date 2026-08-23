"""`rows_expected` must be counted over the same interval the rows are filtered on.

The numerator and denominator disagreed about the end of the range. Rows are
selected half-open (`index < end_utc`); `expected_session_dates` is inclusive of
the end DATE. So whenever `end_utc` fell on midnight of a trading day, that
whole day's bars were filtered out of the numerator while the day was still
counted in full in the denominator.

Measured on real data before the fix — AAPL 5m, Mon 2026-08-17 → Fri
2026-08-21: returned 312, expected 390. The 78-bar gap is exactly Friday.

It is not cosmetic. `coverage_complete` is `rows_returned >= rows_expected`, so
the phantom shortfall flips it False, which demotes the harvest's quality tier
and drives the data-health screen. A grade that moves with where you put the
request boundary — rather than with the data — is worse than no grade, because
it trains people to ignore it.

These tests use a stub plugin rather than the real store so they assert the
arithmetic directly and cannot be knocked over by whatever happens to be on
disk.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tradepro_strategies.bar_cache.store import BarStore

BARS_PER_SESSION = 78          # 5m bars in a 6.5h US session


class _StubPlugin:
    """Mon–Fri are sessions; every session has the same bar count."""

    @staticmethod
    def expected_session_dates(start: datetime, end: datetime) -> list[date]:
        sd = start.date() if isinstance(start, datetime) else start
        ed = end.date() if isinstance(end, datetime) else end
        out, cur = [], sd
        while cur <= ed:                       # INCLUSIVE, like the real plugins
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out

    @staticmethod
    def expected_bar_count(resolution: str, session_date: date) -> int:
        return BARS_PER_SESSION


def _expected(start: datetime, end: datetime) -> int:
    return BarStore._sum_expected_bar_count(_StubPlugin(), "5m", start, end)


def _utc(day: str, hour: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00")


def test_end_at_midnight_does_not_count_that_session():
    """Mon→Fri with end at Friday midnight: Friday's bars are all excluded by
    `index < end_utc`, so Friday must not be counted. Four sessions, not five."""
    got = _expected(_utc("2026-08-17"), _utc("2026-08-21"))
    assert got == 4 * BARS_PER_SESSION, (
        f"expected 4 reachable sessions (Mon-Thu), got {got / BARS_PER_SESSION}"
    )


def test_end_later_in_the_day_does_count_that_session():
    """Same range ending Friday 23:00Z: Friday's bars ARE reachable, so Friday
    counts. This is the half of the fix that stops it under-counting."""
    got = _expected(_utc("2026-08-17"), _utc("2026-08-21", 23))
    assert got == 5 * BARS_PER_SESSION, (
        f"expected 5 sessions (Mon-Fri), got {got / BARS_PER_SESSION}"
    )


def test_weekend_end_is_unaffected():
    """A window ending on a Sunday still counts exactly the week's sessions —
    the weekend days were never sessions, so nothing changes."""
    assert _expected(_utc("2026-08-16"), _utc("2026-08-23")) == 5 * BARS_PER_SESSION
    assert _expected(_utc("2026-08-15"), _utc("2026-08-22")) == 5 * BARS_PER_SESSION


def test_grade_does_not_depend_on_the_day_you_ask():
    """The regression that motivated this: the SAME five sessions of data must
    produce the same expectation whether the window is asked Sat→Sat or
    Sun→Sun. A quality grade that moves with the calendar is a false alarm."""
    saturday_view = _expected(_utc("2026-08-15"), _utc("2026-08-22"))
    sunday_view = _expected(_utc("2026-08-16"), _utc("2026-08-23"))
    assert saturday_view == sunday_view, (
        f"same data graded differently by day of week: "
        f"Sat={saturday_view} Sun={sunday_view}"
    )


def test_empty_range_expects_nothing():
    assert _expected(_utc("2026-08-22"), _utc("2026-08-22")) == 0


def test_harvest_tomorrow_boundary_never_counts_a_phantom_session():
    """The real-world regression.

    `cli/bar_cache_harvest.py` sets `to_date = requested + 1 day`, so `end_utc`
    is always TOMORROW at midnight. Whenever tomorrow is a trading day — every
    run from Sunday through Thursday — that day used to be counted as an
    expected session even though no bar for it can exist yet. Observed on
    2026-08-23: 390 returned against 468 expected, exactly one phantom session,
    flipping all 250 symbols from GOLD to SILVER against unchanged data that had
    graded 249 GOLD the day before.

    Counting only sessions that are actually reachable makes the expectation
    identical whether tomorrow happens to be a trading day or a weekend.
    """
    week = [
        ("2026-08-19", "Wed"), ("2026-08-20", "Thu"), ("2026-08-21", "Fri"),
        ("2026-08-22", "Sat"), ("2026-08-23", "Sun"),
    ]
    start = _utc("2026-08-17")                      # Monday
    seen = {}
    for day, label in week:
        end = _utc(day) + timedelta(days=1)         # harvest's "tomorrow"
        seen[label] = _expected(start, end)

    # Runs on Fri/Sat/Sun all see the same completed week: Mon-Fri.
    assert seen["Fri"] == seen["Sat"] == seen["Sun"] == 5 * BARS_PER_SESSION, seen
    # And a mid-week run counts only the sessions that have actually happened.
    assert seen["Wed"] == 3 * BARS_PER_SESSION, seen
    assert seen["Thu"] == 4 * BARS_PER_SESSION, seen
