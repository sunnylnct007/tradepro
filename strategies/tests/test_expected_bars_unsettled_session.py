"""`rows_expected` must not count a session that has not settled yet.

THE DEFECT, 2026-08-31. Every scheduled 5m harvest from 03:56 onward reported
the identical line:

    bar-cache-harvest  partial  5m 244 sym → 0G/216S/28B/0M

Ten runs, byte-identical, while the previous evening had graded 226 GOLD. The
data was untouched and perfect: AAPL held 390 rows — five sessions x 78 bars,
100% ibkr_web, complete through Friday's close.

The denominator counted TODAY. Reachability (see
test_expected_bars_end_boundary) is a TIMESTAMP test: it drops a session whose
midnight is at or past `end_utc`. It does not drop today, because on a trading
day today's midnight is already in the past the moment the date turns over. So
from 00:00 UTC the store expected a full 78-bar session from a market that does
not open until 13:30 — a shortfall no fetch can ever fill, flipping
`coverage_complete` False and demoting the entire universe GOLD -> SILVER.

This is the same failure the end-boundary docstring already warns about, one
step further along: a grade that moves with the CLOCK rather than with the
data. It is not cosmetic and it is not merely noisy — a run log that cries
"partial" every 35 minutes is precisely how a real outage goes unnoticed.

The fix reuses `_last_settled_session`, which the FETCH path already used to
avoid requesting today's bars. One half of the store knew today's bar does not
exist yet; the counting half asked for it anyway.

These tests pass `now_utc` explicitly so they assert the arithmetic against a
fixed clock, and cannot pass or fail depending on when they are run.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tradepro_strategies.bar_cache.store import BarStore

BARS_PER_SESSION = 78
CLOSE_HOUR = 20            # 16:00 ET in summer


class _Plugin:
    """Mon-Fri sessions, a modelled 20:00Z close — like the real US plugins."""

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

    @staticmethod
    def session_close_utc(session_date: date) -> datetime:
        return datetime(session_date.year, session_date.month, session_date.day,
                        CLOSE_HOUR, tzinfo=timezone.utc)


class _PluginWithoutClose(_Plugin):
    """A 24h venue: models no close, so nothing can be called settled."""
    session_close_utc = None

    def __getattribute__(self, name):
        if name == "session_close_utc":
            raise AttributeError(name)
        return object.__getattribute__(self, name)


def _utc(day: str, hour: int = 0) -> datetime:
    return datetime.fromisoformat(f"{day}T{hour:02d}:00:00+00:00")


def _expected(start, end, now, plugin=None):
    return BarStore._sum_expected_bar_count(
        plugin or _Plugin(), "5m", start, end, now_utc=now)


# Mon 2026-08-24 .. Fri 2026-08-28 are sessions; Mon 2026-08-31 is the next one.
WEEK_START = _utc("2026-08-24")


def test_today_before_the_open_is_not_expected():
    """THE regression. Monday 08:00Z, five settled sessions behind us: the
    expectation is five, not six. Before the fix this counted Monday and every
    symbol graded SILVER against complete data."""
    end = _utc("2026-08-31") + timedelta(days=1)      # harvest's "tomorrow"
    got = _expected(WEEK_START, end, now=_utc("2026-08-31", 8))
    assert got == 5 * BARS_PER_SESSION, (
        f"expected the 5 settled sessions, got {got / BARS_PER_SESSION}")


def test_today_mid_session_is_still_not_expected():
    """15:00Z — the market is OPEN but the session has not closed. Its bars are
    not final, so it cannot be counted whole; counting it would demote every
    symbol for the entire trading day."""
    end = _utc("2026-08-31") + timedelta(days=1)
    got = _expected(WEEK_START, end, now=_utc("2026-08-31", 15))
    assert got == 5 * BARS_PER_SESSION, (
        f"an open session was counted as complete: {got / BARS_PER_SESSION}")


def test_today_after_the_close_is_expected():
    """21:00Z, after the 20:00Z close. Now Monday's bars are final and MUST be
    demanded — otherwise the clamp would hide a genuinely missing session."""
    end = _utc("2026-08-31") + timedelta(days=1)
    got = _expected(WEEK_START, end, now=_utc("2026-08-31", 21))
    assert got == 6 * BARS_PER_SESSION, (
        f"a settled session was not demanded: {got / BARS_PER_SESSION}")


def test_grade_does_not_move_with_the_clock():
    """The property that was violated. The same five settled sessions must grade
    identically whether asked at 04:00, 08:00 or noon on the following Monday —
    the ten byte-identical `0G/216S/28B` runs were this assertion failing."""
    end = _utc("2026-08-31") + timedelta(days=1)
    views = {h: _expected(WEEK_START, end, now=_utc("2026-08-31", h))
             for h in (4, 8, 12)}
    assert len(set(views.values())) == 1, f"grade moved with the clock: {views}"
    assert set(views.values()) == {5 * BARS_PER_SESSION}, views


def test_weekend_run_is_unaffected():
    """Sunday: no session to clamp, so the completed week still counts five.
    The clamp must not eat settled sessions."""
    end = _utc("2026-08-30") + timedelta(days=1)
    got = _expected(WEEK_START, end, now=_utc("2026-08-30", 23))
    assert got == 5 * BARS_PER_SESSION, got


def test_a_venue_with_no_modelled_close_is_left_alone():
    """`_last_settled_session` treats an unmodelled close as 'cannot tell'. A
    plugin that does not define the method at all is the same situation and must
    behave the same way rather than raising — the clamp is a refinement, not a
    requirement."""
    end = _utc("2026-08-31") + timedelta(days=1)
    got = _expected(WEEK_START, end, now=_utc("2026-08-31", 8),
                    plugin=_PluginWithoutClose())
    assert got == 6 * BARS_PER_SESSION, (
        f"a plugin without a modelled close should be left unclamped: {got}")
