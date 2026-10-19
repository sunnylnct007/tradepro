"""A non-trading day must not be fetched, and must not read as a data fault.

WHAT IT COST, 17-19 Oct 2026. `_lookback_dates` has always filtered to business
days via bdate_range — EXCEPT on the single-day path (lookback_days == 0),
which returned the date unchanged. So over one weekend the swing lane asked
both sources for Saturday's and Sunday's bars, for every symbol in the
universe, and raised DataUnavailableError per symbol AFTER calling them:

    Fri 16 Oct    9,882 log lines
    Sat 17 Oct   25,260
    Sun 18 Oct   54,318      "NO BARS for 917 symbol(s) on 2026-10-18"
    Mon 19 Oct      120      one pass, finishing 20:20 UTC

Monday's pass did not complete until twenty minutes AFTER the close. Three
exits — one tagged `swing exit target` — missed an entire trading day.

Every health check reported the desk green throughout: the lanes WERE running
and orders WERE working at the broker. Nothing measured whether the work
happened in time.

WHY [] AND NOT THE NEAREST BUSINESS DAY. Serving Friday's bar when Sunday was
asked for is a silent substitution of a different day's data. No candidates
means no bars, which is the truth about a Sunday.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradepro_strategies.paper.sources import base as B
from tradepro_strategies.paper.sources.errors import DataUnavailableError


SAT = dt.datetime(2026, 10, 17)
SUN = dt.datetime(2026, 10, 18)
MON = dt.datetime(2026, 10, 19)
FRI = dt.datetime(2026, 10, 16)


@pytest.mark.parametrize("day", [SAT, SUN])
def test_a_weekend_single_day_request_has_no_candidates(day):
    assert B._lookback_dates(day, 0) == []


@pytest.mark.parametrize("day", [FRI, MON])
def test_a_weekday_is_unchanged(day):
    assert B._lookback_dates(day, 0) == [day]


def test_the_lookback_path_is_untouched():
    """It already filtered weekends; this change must not alter it."""
    out = B._lookback_dates(MON, 5)
    assert out, "lookback window went empty"
    assert all(d.weekday() < 5 for d in out), [d.strftime("%a") for d in out]


class _Source:
    """Records whether anything actually asked it for data."""
    def __init__(self): self.calls = 0
    async def fetch(self, *a, **k):
        self.calls += 1
        return []


def test_no_source_is_CALLED_on_a_closed_market():
    """The 917-symbols-x-2-sources waste, in one assertion.

    asyncio.run rather than a pytest-asyncio marker: that plugin is not
    registered in this suite, and an unknown marker silently SKIPS the body —
    a guard that never runs is worse than no guard.
    """
    import asyncio
    src = _Source()
    with pytest.raises(DataUnavailableError):
        asyncio.run(B._fetch_window(src, "AAPL", SUN, "1d", 0))
    assert src.calls == 0, (
        f"called the source {src.calls} time(s) for a Sunday — this is the "
        "fetch storm that cost a Monday session")


def test_a_weekday_still_reaches_the_source():
    """Guarding the guard: if this stops calling, the lane stops trading."""
    import asyncio
    src = _Source()
    with pytest.raises(DataUnavailableError):
        asyncio.run(B._fetch_window(src, "AAPL", MON, "1d", 0))
    assert src.calls >= 1, "a trading day no longer reaches the source"


def test_closed_days_are_counted_once_not_per_symbol():
    B._CLOSED.clear()
    for _ in range(917):
        B._record_closed(SUN)
    assert len(B._CLOSED) == 1
    assert list(B._CLOSED.values()) == [917], (
        "the count is the point — 917 silent names is a shut market, and the "
        "number is what tells that apart from a broken feed")
    B._CLOSED.clear()
