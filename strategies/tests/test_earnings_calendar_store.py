"""Store-first earnings-date resolution (migration 062, owner ruling 10 Aug 2026).

The central earnings_calendar store is consulted BEFORE the live per-symbol
Finnhub proxy; an AUTHORITATIVE store answers alone (including "no row =
earnings-clear"), while an empty/stale/unreachable store falls back to the
live path. The authority rule is the load-bearing part: an empty store must
never read as earnings-clear (fail-open trap).
"""
from __future__ import annotations

import datetime as dt

import pytest

from tradepro_strategies import earnings


TODAY = dt.date.today()


def _store_payload(events, *, total_rows=5000, last_upload_hours_ago=6):
    last = (
        dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=last_upload_hours_ago)
    ).isoformat()
    return {
        "symbol": "NVDA",
        "events": events,
        "store": {"totalRows": total_rows, "symbols": 900, "lastUploadUtc": last},
    }


@pytest.fixture(autouse=True)
def _clear_store_cache():
    earnings._STORE_CACHE.clear()
    yield
    earnings._STORE_CACHE.clear()


def test_authoritative_store_serves_upcoming_without_live_call(monkeypatch):
    future = (TODAY + dt.timedelta(days=9)).isoformat()
    monkeypatch.setattr(
        earnings, "_calendar_store_events",
        lambda sym, base, **kw: _store_payload(
            [{"report_date": future, "session": "amc", "source": "finnhub_bulk"}]),
    )

    def _boom(*a, **kw):  # the live proxy must NOT be hit
        raise AssertionError("live per-symbol call fired despite authoritative store")
    monkeypatch.setattr("requests.get", _boom)

    up = earnings.fetch_upcoming_earnings("NVDA", "http://api")
    assert up is not None
    assert up["date"] == future
    assert up["days_until"] == 9
    assert up["hour"] == "amc"
    assert up["_source"].startswith("store://")


def test_authoritative_store_with_no_future_row_is_clear_not_fallthrough(monkeypatch):
    past = (TODAY - dt.timedelta(days=20)).isoformat()
    monkeypatch.setattr(
        earnings, "_calendar_store_events",
        lambda sym, base, **kw: _store_payload(
            [{"report_date": past, "session": "bmo", "source": "finnhub_bulk"}]),
    )

    def _boom(*a, **kw):
        raise AssertionError("live call fired — the rate-limit storm the store ends")
    monkeypatch.setattr("requests.get", _boom)

    assert earnings.fetch_upcoming_earnings("NVDA", "http://api") is None


@pytest.mark.parametrize("meta", [
    None,                                                          # store call failed
    {"totalRows": 3, "symbols": 3, "lastUploadUtc":                # near-empty store
     dt.datetime.now(dt.timezone.utc).isoformat()},
    {"totalRows": 5000, "symbols": 900, "lastUploadUtc":           # stale harvest
     (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).isoformat()},
    {"totalRows": 5000, "symbols": 900, "lastUploadUtc": None},    # never harvested
])
def test_non_authoritative_store_is_not_treated_as_clear(meta):
    assert earnings._store_is_authoritative(meta) is False


def test_last_report_from_store_returns_most_recent_past(monkeypatch):
    d1 = (TODAY - dt.timedelta(days=25)).isoformat()
    d2 = (TODAY - dt.timedelta(days=2)).isoformat()
    future = (TODAY + dt.timedelta(days=40)).isoformat()
    monkeypatch.setattr(
        earnings, "_calendar_store_events",
        lambda sym, base, **kw: _store_payload([
            {"report_date": d1}, {"report_date": d2}, {"report_date": future},
        ]),
    )
    assert earnings.last_report_from_store("NVDA", "http://api") == d2


def test_last_report_from_store_none_when_store_stale(monkeypatch):
    d = (TODAY - dt.timedelta(days=2)).isoformat()
    monkeypatch.setattr(
        earnings, "_calendar_store_events",
        lambda sym, base, **kw: _store_payload(
            [{"report_date": d}], last_upload_hours_ago=24 * 10),
    )
    assert earnings.last_report_from_store("NVDA", "http://api") is None
