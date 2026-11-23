"""23 Nov 2026: MU's 5m store ended on Friday; the rotation board printed
Friday's −13.9% as "today" beside a live price up 18%. "Today" may only
describe the live session."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from tradepro_strategies.paper.strategies import momentum_rotation as MR

ET = ZoneInfo("America/New_York")


def _closes():
    idx = pd.to_datetime(["2026-11-18", "2026-11-19", "2026-11-20"])
    return pd.Series([1.0, 2.0, 3.0], index=idx)


def _freeze(monkeypatch, *, when):
    class _Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return when if tz is None else when.astimezone(tz)
    import datetime as _dt
    monkeypatch.setattr(_dt, "datetime", _Now)


def test_during_a_weekday_session_today_is_the_new_york_date(monkeypatch):
    _freeze(monkeypatch, when=datetime(2026, 11, 23, 13, 5, tzinfo=ET))  # Monday 13:05 ET
    assert MR._live_session(_closes()) == "2026-11-23"


def test_before_the_open_and_on_weekends_today_is_the_last_settled_close(monkeypatch):
    _freeze(monkeypatch, when=datetime(2026, 11, 23, 8, 0, tzinfo=ET))  # Monday pre-market
    assert MR._live_session(_closes()) == "2026-11-20"
    _freeze(monkeypatch, when=datetime(2026, 11, 22, 15, 0, tzinfo=ET))  # Sunday
    assert MR._live_session(_closes()) == "2026-11-20"


def test_nothing_settled_means_no_today_outside_a_session(monkeypatch):
    _freeze(monkeypatch, when=datetime(2026, 11, 22, 15, 0, tzinfo=ET))  # Sunday
    assert MR._live_session(pd.Series(dtype=float)) is None
