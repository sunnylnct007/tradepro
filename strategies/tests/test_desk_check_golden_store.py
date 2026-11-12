"""desk-check's golden-store check: judged against the store's own calendar.

Replaces the idle-machinery watch on the retired API bar harvester. It reads
the same candles endpoint every screen reads, and must never call a holiday
stale nor call an unreadable store fine.
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradepro_strategies.cli import desk_check as dc

UTC = dt.timezone.utc
# Wednesday 12 Nov 2026, 21:45Z — when the lane runs.
NOW = dt.datetime(2026, 11, 12, 21, 45, tzinfo=UTC)


def _reply(**kw):
    base = {"provider": "bar_cache", "location": "s3://b/bar_cache", "note": None,
            "newestBar": "2026-11-11T14:30:00Z", "sources": {"ibkr_web": 9}}
    base.update(kw)
    return base


@pytest.fixture()
def serve(monkeypatch):
    box = {}
    monkeypatch.setattr(dc, "_get", lambda base, token, path, timeout=30: box["reply"])
    return box


def test_last_settled_session_present_is_ok(serve):
    serve["reply"] = _reply()
    [c] = dc.check_golden_store("http://api", None, now_utc=NOW)
    assert c.status == dc.OK, c


def test_a_missed_session_is_a_warning_that_names_both_dates(serve):
    serve["reply"] = _reply(newestBar="2026-11-09T14:30:00Z")
    [c] = dc.check_golden_store("http://api", None, now_utc=NOW)
    assert c.status == dc.WARN
    assert "2026-11-09" in c.detail and "2026-11-11" in c.detail


def test_thanksgiving_is_not_stale(serve):
    # Fri 27 Nov 2026, the day after Thanksgiving (Thu 26 Nov, market shut):
    # the last settled session before Friday is Wed 25 Nov.
    serve["reply"] = _reply(newestBar="2026-11-25T14:30:00Z")
    [c] = dc.check_golden_store("http://api", None,
                                now_utc=dt.datetime(2026, 11, 27, 21, 45, tzinfo=UTC))
    assert c.status == dc.OK, c


def test_an_unreadable_store_is_broken_not_ok(serve):
    serve["reply"] = _reply(newestBar=None, note="bar store at s3://b could not be read: AmazonS3Exception")
    [c] = dc.check_golden_store("http://api", None, now_utc=NOW)
    assert c.status == dc.BROKEN and "could not be read" in c.detail


def test_candles_from_another_provider_is_a_warning(serve):
    serve["reply"] = _reply(provider="yahoo")
    [c] = dc.check_golden_store("http://api", None, now_utc=NOW)
    assert c.status == dc.WARN and "yahoo" in c.detail
