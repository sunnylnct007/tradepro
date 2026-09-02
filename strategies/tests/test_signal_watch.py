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


# ── EXITS (2 Sep 2026) ──────────────────────────────────────────────────────
#
# Owner: "and will the sugnals close by them seleves after profit booking".
#
# They did not, and that was worse than it sounds. Both strategies DEFINE their
# exits — Swing targets +3.1% with a 20-session cap, Momentum trails 8% with a
# 60-session cap — and nothing executed them. Positions would have sat open
# indefinitely, so tradepro-trade-eval would have scored BUY-AND-HOLD rather
# than the strategy, confidently and wrongly.
#
# An edge measured over a holding period is not the same trade when held longer.

def _paper(sym, **kw):
    base = {"id": "abc", "symbol": sym, "side": "BUY", "qty": 10, "state": "FILLED",
            "broker": "PAPER", "strategyId": "candidates_swing",
            "signalStopPrice": 267.32, "signalRefPrice": 290.57}
    base.update(kw)
    return base


def test_a_reached_target_closes_the_paper_position(monkeypatch):
    _orders(monkeypatch, [_paper("IWM", signalTargetPrice=299.58)])
    monkeypatch.setattr(SW, "_last_close", lambda s: (301.0, "2026-09-20"))
    closed = {}
    monkeypatch.setattr(SW, "_close_paper", lambda b, h, o, p: closed.setdefault("x", True))
    ev = SW.check("http://api.test", None)
    assert ev[0]["kind"] == "TARGET REACHED"
    assert closed.get("x") is True
    assert "CLOSED" in ev[0]["text"]


def test_a_breached_stop_also_closes(monkeypatch):
    _orders(monkeypatch, [_paper("IWM")])
    monkeypatch.setattr(SW, "_last_close", lambda s: (260.0, "2026-09-20"))
    monkeypatch.setattr(SW, "_close_paper", lambda b, h, o, p: True)
    ev = SW.check("http://api.test", None)
    assert ev[0]["kind"] == "STOP BREACHED" and "CLOSED" in ev[0]["text"]


def test_held_past_the_strategys_own_cap_closes(monkeypatch):
    """20 sessions is Swing's OWN published max_hold, not a number invented
    here. Past its window the edge was never measured."""
    _orders(monkeypatch, [_paper("IWM", signalBar="2026-01-05")])
    monkeypatch.setattr(SW, "_last_close", lambda s: (285.0, "2026-09-20"))
    monkeypatch.setattr(SW, "_close_paper", lambda b, h, o, p: True)
    ev = SW.check("http://api.test", None)
    assert ev[0]["kind"] == "HELD TOO LONG"
    assert "20-session cap" in ev[0]["text"]


def test_a_position_inside_all_three_rules_is_left_alone(monkeypatch):
    _orders(monkeypatch, [_paper("IWM", signalTargetPrice=299.58,
                                 signalBar=_dt_today_iso())])
    monkeypatch.setattr(SW, "_last_close", lambda s: (291.0, "2026-09-02"))
    assert SW.check("http://api.test", None) == []


def test_a_LIVE_position_is_never_closed(monkeypatch):
    """The check is on the BROKER, not the strategy id. A watcher must not be
    able to close real money on any code path."""
    called = {}
    import requests
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: called.setdefault("posted", True))
    ok = SW._close_paper("http://api.test", {},
                         _paper("IWM", broker="IBKR_LIVE"), 260.0)
    assert ok is False
    assert "posted" not in called, "a live position must never be closed here"


def test_a_failed_close_still_alerts_and_says_so(monkeypatch):
    """The alert is the fallback. If the close fails the owner must be told to
    do it by hand, not left believing it was handled."""
    _orders(monkeypatch, [_paper("IWM")])
    monkeypatch.setattr(SW, "_last_close", lambda s: (260.0, "2026-09-20"))
    monkeypatch.setattr(SW, "_close_paper", lambda b, h, o, p: False)
    ev = SW.check("http://api.test", None)
    assert "close FAILED" in ev[0]["text"] and "by hand" in ev[0]["text"]


def _dt_today_iso():
    import datetime as d
    return d.date.today().isoformat()
