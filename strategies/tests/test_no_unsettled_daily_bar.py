"""A DAILY bar for a session still in progress must never be written.

THE DEFECT, 1 Sep 2026, measured 1h26m into an open US session:

    AAPL  ... 2026-08-31 : ibkr_web : 316.85     <- correctly ends at Monday
    ACN   ... 2026-08-31 : ibkr_web : 189.76
          ... 2026-09-01 : yfinance : 188.85     <- TODAY, mid-session, Yahoo

50 of 244 symbols carried a daily bar for a session that had not finished,
sourced from the fallback provider. HV30, the Ichimoku regime and the 52-week
range all read it as a completed day, and it is the standing source of the
mixed-provider tails behind the desk's FALLBACK badges.

THE CAUSE. `store.get` clamped the fetch window to the last SETTLED session —
but only inside `if delta_from is not None and not force_refresh`. A cache miss
took the unclamped path, asked for today, and then played out exactly the
failure already written in that function's own comment: IBKR answers "none
within range", the chain classifies that correct answer as a parse failure,
falls through, and yfinance writes the partition.

One half of the store knew today's bar does not exist yet. The other half asked
for it anyway — the same asymmetry, now closed on the scheduled path too.

DAILY-OR-COARSER ONLY. An intraday lane legitimately wants today's partial bars;
that is what a 5m harvest is for. A DAILY bar for a session in progress is not
early data, it is a wrong number wearing a date.

NOT ON force_refresh, deliberately: a fetch_window converts a full-partition
REPLACE into a windowed merge, defeating the validated shrink that rebuilds a
poisoned partition (see test_force_refresh_validated_shrink_replaces_phantom_rows).
An explicit re-source is an operator naming a range.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from tradepro_strategies.bar_cache.store import BarStore

UTC = timezone.utc
CLOSE_H = 20                      # 16:00 ET in summer


class _Plugin:
    @staticmethod
    def expected_session_dates(start, end):
        sd = start.date() if isinstance(start, datetime) else start
        ed = end.date() if isinstance(end, datetime) else end
        out, cur = [], sd
        while cur <= ed:
            if cur.weekday() < 5:
                out.append(cur)
            cur += timedelta(days=1)
        return out

    @staticmethod
    def expected_bar_count(resolution, session_date):
        return 1

    @staticmethod
    def session_close_utc(d: date) -> datetime:
        return datetime(d.year, d.month, d.day, CLOSE_H, tzinfo=UTC)


def _settled(start, end, now):
    return BarStore._last_settled_session(_Plugin(), start, end, now_utc=now)


def test_mid_session_today_is_not_settled():
    """14:56 UTC on Tue 1 Sep — the exact moment 50 symbols got a Yahoo bar for
    a session that was 1h26m old. The cap must stop at Monday."""
    cap = _settled(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC),
                   now=datetime(2026, 9, 1, 14, 56, tzinfo=UTC))
    # Every session in range is unsettled -> the window start is returned.
    assert cap == datetime(2026, 9, 1, tzinfo=UTC)


def test_after_the_close_today_is_settled():
    """21:00 UTC. Now today's bar is final and MUST be fetchable — the clamp
    may not hide a session that really has ended."""
    cap = _settled(datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 2, tzinfo=UTC),
                   now=datetime(2026, 9, 1, 21, 0, tzinfo=UTC))
    assert cap == datetime(2026, 9, 2, tzinfo=UTC)


def test_a_window_ending_yesterday_is_untouched():
    """The 194 symbols that were already correct must stay correct."""
    cap = _settled(datetime(2026, 8, 24, tzinfo=UTC), datetime(2026, 8, 29, tzinfo=UTC),
                   now=datetime(2026, 9, 1, 14, 56, tzinfo=UTC))
    assert cap == datetime(2026, 8, 29, tzinfo=UTC)


def test_the_clamp_is_wired_into_the_non_delta_path():
    """Structural: the fix must sit OUTSIDE the delta-mode branch, and must not
    fire on force_refresh."""
    import inspect
    src = inspect.getsource(BarStore.get)
    assert "unsettled_skip" in src or "settled_cap" in src
    assert "and not force_refresh" in src and "_is_daily_or_coarser(resolution)" in src
