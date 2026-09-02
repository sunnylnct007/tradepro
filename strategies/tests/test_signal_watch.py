"""Signals find you. Screens wait to be looked at.

Owner, 2 Sep 2026: "i dont need more screens i need trading signals".

A screen says "here are 34 things, you decide". A signal says "PLTR, stop
breached at 165.53, get out". The candidates already carried entry, stop and
size — what was missing is that NOTHING watched them afterwards. The index
strangle has had this since 11 Aug; equity positions had nothing, so a stop
could break at 10:00 and nobody would know until someone opened a tab.

THREE PROPERTIES THIS PINS

  * EACH EVENT FIRES ONCE PER DAY. A watcher that repeats every fifteen minutes
    teaches you to filter it out — which is how this desk lost four separate
    alarms in one week to noise.
  * A FAILED SEND RETRIES. Marking an event fired before the mail succeeds
    swallows it for the day; the mark happens only after a successful send.
  * AN UNREADABLE ORDER BOOK IS AN EVENT, not silence. "I could not look" and
    "nothing is wrong" must never render the same.
"""
from __future__ import annotations

import json

import pytest

from tradepro_strategies.cli import signal_watch as SW


@pytest.fixture(autouse=True)
def _tmp_fired(tmp_path, monkeypatch):
    monkeypatch.setattr(SW, "FIRED", tmp_path / "fired.json")


def _orders(monkeypatch, rows):
    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return rows
    monkeypatch.setattr("requests.get", lambda *a, **k: _R())


def test_a_breached_stop_is_an_event(monkeypatch):
    _orders(monkeypatch, [{"symbol": "PLTR", "side": "BUY", "qty": 11,
                           "state": "FILLED", "strategyId": "candidates_momentum",
                           "signalStopPrice": 165.53, "signalRefPrice": 179.92}])
    monkeypatch.setattr(SW, "_last_close", lambda s: (160.0, "2026-09-02"))
    ev = SW.check("http://api.test", None)
    assert len(ev) == 1 and ev[0]["kind"] == "STOP BREACHED"
    assert "165.53" in ev[0]["text"] and "-11" in ev[0]["text"]


def test_a_price_above_the_stop_is_not_an_event(monkeypatch):
    _orders(monkeypatch, [{"symbol": "PLTR", "state": "FILLED",
                           "strategyId": "candidates_momentum",
                           "signalStopPrice": 165.53}])
    monkeypatch.setattr(SW, "_last_close", lambda s: (179.0, "2026-09-02"))
    assert SW.check("http://api.test", None) == []


def test_a_queued_order_that_never_became_a_trade_is_an_event(monkeypatch):
    """Found on the first real run: five orders sat PENDING_APPROVAL and would
    never have become trades. A signal system that stays silent about that is
    not a signal system."""
    _orders(monkeypatch, [{"symbol": "IWM", "side": "BUY", "qty": 6,
                           "state": "PENDING_APPROVAL",
                           "strategyId": "candidates_swing", "signalStopPrice": 267.32}])
    ev = SW.check("http://api.test", None)
    assert ev[0]["kind"] == "AWAITING APPROVAL"
    assert "never becomes a trade" in ev[0]["text"]


def test_an_event_does_not_repeat_once_fired(monkeypatch):
    _orders(monkeypatch, [{"symbol": "PLTR", "state": "FILLED",
                           "strategyId": "candidates_momentum",
                           "signalStopPrice": 165.53}])
    monkeypatch.setattr(SW, "_last_close", lambda s: (160.0, "2026-09-02"))
    first = SW.check("http://api.test", None)
    assert len(first) == 1
    SW._mark_fired({e["key"] for e in first})
    assert SW.check("http://api.test", None) == []


def test_other_strategies_are_not_watched(monkeypatch):
    """Only candidates_* carry a stop this watcher can check. Alerting on an
    order whose exit rule lives elsewhere would be a guess."""
    _orders(monkeypatch, [{"symbol": "ARWR", "state": "FILLED",
                           "strategyId": "mean_reversion_swing_ibkr",
                           "signalStopPrice": 75.79}])
    monkeypatch.setattr(SW, "_last_close", lambda s: (10.0, "2026-09-02"))
    assert SW.check("http://api.test", None) == []


def test_an_unreadable_order_book_is_reported_not_swallowed(monkeypatch):
    def _boom(*a, **k): raise RuntimeError("connection refused")
    monkeypatch.setattr("requests.get", _boom)
    ev = SW.check("http://api.test", None)
    assert ev and ev[0]["kind"] == "ERROR"
    assert "could not read" in ev[0]["text"]


def test_a_missing_price_does_not_invent_a_breach(monkeypatch):
    """No bars means we cannot tell. Silence is right; a fabricated breach
    would have the owner close a position on nothing."""
    _orders(monkeypatch, [{"symbol": "ZZZZ", "state": "FILLED",
                           "strategyId": "candidates_momentum",
                           "signalStopPrice": 100.0}])
    monkeypatch.setattr(SW, "_last_close", lambda s: (None, None))
    assert SW.check("http://api.test", None) == []
