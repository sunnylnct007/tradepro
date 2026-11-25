"""A position with a SELL already working at the OMS is not exited again.

25 Nov 2026, 21:22 UTC: the rotation sleeve's AMD stop exit was placed and sat
PreSubmitted at IBKR for the next session (Thanksgiving in between). 21:38:
the daily bar had rolled, the next tick re-decided the same exit, the
idempotency key (seeded from the bar) changed, the OMS in-flight dedupe only
looks back 15 minutes, and a SECOND SELL 7 was enqueued. Approved on top of
the first fill it would have left the sleeve short 7 — the 21 Sep ARWR and
1 Oct ESNT runaways in miniature. `has_order_in_flight` is in-process and the
daemon is a new process every 15 minutes; only the OMS knows what is working.

Rule: one working exit per position. The DECISION is still logged; only the
ORDER is withheld. If the OMS cannot be read, hold — a late exit costs one
bar, a doubled one is a short.
"""
from __future__ import annotations

from datetime import UTC, datetime

from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy)
from tradepro_strategies.paper.strategy import Bar

CLOSES = [100.0] * 30 + [80.0]          # an exit the signals will take
DATES = [f"2026-10-{d:02d}" for d in range(1, 32)]


def _strategy(monkeypatch, working):
    s = MeanReversionSwingStrategy(strategy_id="momentum_rotation_ibkr")
    s.seed_positions({"AMD": 7}, {})
    s._fill_price["AMD"] = 511.04
    s._entry_bar["AMD"] = "2026-11-16"

    def _history(sym, bar):
        s._dates = list(DATES)
        return list(CLOSES), None

    monkeypatch.setattr(s, "_history", _history)
    monkeypatch.setattr(s, "_broker_positions", lambda: {"AMD": 7.0})
    monkeypatch.setattr(s.SIGNALS, "exit_decision",
                        lambda closes, i, **k: (True, "trailing-stop"))
    monkeypatch.setattr(s, "_working_sells", lambda: working)
    return s


def _bar():
    return Bar(symbol="AMD", timestamp=datetime(2026, 11, 25, 5, tzinfo=UTC),
               open=413.0, high=413.0, low=413.0, close=413.27, volume=0, timeframe_seconds=86400)


def _actions(s):
    return [d["action"] for d in s.recent_decisions()]


def test_no_working_sell_means_the_exit_is_raised(monkeypatch):
    s = _strategy(monkeypatch, working={})
    orders = s.on_bar(_bar())
    assert len(orders) == 1 and orders[0].side.value == "SELL" and orders[0].quantity == 7
    assert "exit-trailing-stop" in _actions(s)


def test_a_working_sell_withholds_the_order_but_keeps_the_decision(monkeypatch):
    s = _strategy(monkeypatch, working={"AMD": {
        "id": "72b0c1bf-c0c8-467c-8322-3351cdd8239a", "state": "SUBMITTED", "qty": 7.0,
        "brokerOrderId": "125427032", "createdAtUtc": "2026-11-25T21:22:23Z"}})
    assert s.on_bar(_bar()) == []
    acts = _actions(s)
    assert "exit-trailing-stop" in acts and "exit-already-working" in acts
    reason = next(d["reason"] for d in s.recent_decisions() if d["action"] == "exit-already-working")
    assert "SUBMITTED" in reason and "125427032" in reason


def test_an_unreadable_oms_holds_rather_than_guesses(monkeypatch):
    s = _strategy(monkeypatch, working=None)
    assert s.on_bar(_bar()) == []
    assert "hold-oms-unreadable" in _actions(s)


def test_working_sells_reads_live_states_only(monkeypatch):
    """PENDING_APPROVAL, SUBMITTED, WORKING, PARTIALLY_FILLED count; FILLED,
    CANCELLED, REJECTED and BUYs do not; other strategies' rows do not."""
    import tradepro_strategies.paper.strategies.mean_reversion_swing as M
    rows = [
        {"strategyId": "momentum_rotation_ibkr", "symbol": "AMD_US_EQ", "side": "SELL", "state": "SUBMITTED", "id": "a"},
        {"strategyId": "momentum_rotation_ibkr", "symbol": "MU_US_EQ", "side": "SELL", "state": "FILLED", "id": "b"},
        {"strategyId": "momentum_rotation_ibkr", "symbol": "TGT_US_EQ", "side": "SELL", "state": "CANCELLED", "id": "c"},
        {"strategyId": "momentum_rotation_ibkr", "symbol": "DELL_US_EQ", "side": "BUY", "state": "SUBMITTED", "id": "d"},
        {"strategyId": "swing_ibkr", "symbol": "VLO_US_EQ", "side": "SELL", "state": "SUBMITTED", "id": "e"},
        {"strategyId": "momentum_rotation_ibkr", "symbol": "PSX_US_EQ", "side": "SELL", "state": "PENDING_APPROVAL", "id": "f"},
    ]

    class _R:
        status_code = 200
        def json(self): return rows

    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
    monkeypatch.setattr("tradepro_strategies.cli.push_to_api.load_credentials", lambda: ("http://api", "t"))
    s = M.MeanReversionSwingStrategy(strategy_id="momentum_rotation_ibkr")
    assert sorted(s._working_sells()) == ["AMD", "PSX"]


def test_working_sells_is_none_when_the_oms_cannot_be_read(monkeypatch):
    import tradepro_strategies.paper.strategies.mean_reversion_swing as M
    monkeypatch.setattr("tradepro_strategies.cli.push_to_api.load_credentials",
                        lambda: (_ for _ in ()).throw(RuntimeError("no creds")))
    s = M.MeanReversionSwingStrategy(strategy_id="momentum_rotation_ibkr")
    assert s._working_sells() is None
