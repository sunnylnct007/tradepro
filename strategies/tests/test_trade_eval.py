"""Is any of this actually working? — the question a backtest cannot answer.

Owner, 2 Sep 2026: "we need trade evalation", after "again the platform at the
current stage gives me nothinbg".

Everything on this desk quotes a BACKTEST — Momentum's 48.8% over 5,396 trades,
Swing's six gates, the wheel's DO NOT FUND — and nothing said what the LIVE
signals did. The owner was being asked to trust numbers measured in the past, on
data this session found four separate faults in.

THREE REFUSALS, each pinned here:

  * NO WIN RATE ON A HANDFUL OF TRADES. 67% on three trades is noise wearing a
    number, and this desk has rejected six strategy candidates for exactly that
    kind of claim. Below the floor the answer is "too few to say".
  * AN OPEN POSITION IS NOT A WIN. An unrealised gain is not a result — the
    owner's own P&L card already draws that line ("Open is soft").
  * A MISSING PRICE IS UNKNOWN, NOT ZERO. The entire session has been a lesson
    in what a defaulted zero does to a screen.
"""
from __future__ import annotations

import pytest

from tradepro_strategies.cli import trade_eval as TE


def _orders(monkeypatch, rows):
    class _R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return rows
    monkeypatch.setattr("requests.get", lambda *a, **k: _R())


def _o(sym, ref, sid="candidates_momentum", state="FILLED"):
    return {"symbol": sym, "strategyId": sid, "state": state, "signalRefPrice": ref}


def test_a_win_rate_is_withheld_below_the_floor(monkeypatch):
    """THE refusal. Four signals cannot produce a win rate worth printing."""
    _orders(monkeypatch, [_o(f"S{i}", 100.0) for i in range(4)])
    monkeypatch.setattr(TE, "_last_close", lambda s: 110.0)
    s = TE.evaluate("http://api.test", None)["strategies"][0]
    assert s["live_win_pct"] is None
    assert "too few to say" in s["live_win_note"]
    # The average move IS reported — it is a measurement, not a rate.
    assert s["live_avg_move_pct"] == 10.0


def test_a_win_rate_appears_once_there_are_enough(monkeypatch):
    _orders(monkeypatch, [_o(f"S{i}", 100.0) for i in range(12)])
    monkeypatch.setattr(TE, "_last_close", lambda s: 110.0)
    s = TE.evaluate("http://api.test", None)["strategies"][0]
    assert s["live_win_pct"] == 100.0 and s["live_win_note"] is None


def test_open_positions_are_marked_open(monkeypatch):
    _orders(monkeypatch, [_o("PLTR", 179.92, state="SUBMITTED")])
    monkeypatch.setattr(TE, "_last_close", lambda s: 190.0)
    s = TE.evaluate("http://api.test", None)["strategies"][0]
    assert s["open"] == 1
    assert s["positions"][0]["open"] is True
    assert "(open)" in TE.render({"as_of": "2026-09-02T13:00", "strategies": [s]})


def test_a_missing_price_is_unknown_not_zero(monkeypatch):
    _orders(monkeypatch, [_o("ZZZZ", 100.0)])
    monkeypatch.setattr(TE, "_last_close", lambda s: None)
    s = TE.evaluate("http://api.test", None)["strategies"][0]
    assert s["positions"][0]["move_pct"] is None
    assert s["n_priced"] == 0
    assert s["live_avg_move_pct"] is None


def test_the_claim_is_shown_beside_the_live_record(monkeypatch):
    """The whole point: a strategy whose live record diverges from its backtest
    is the most useful thing this platform can say."""
    _orders(monkeypatch, [_o("PLTR", 100.0)])
    monkeypatch.setattr(TE, "_last_close", lambda s: 90.0)
    out = TE.render(TE.evaluate("http://api.test", None))
    assert "claim  win 48.8%" in out and "5,396" in out
    assert "51% of trades LOSE" in out


def test_other_strategies_are_ignored(monkeypatch):
    _orders(monkeypatch, [_o("ARWR", 80.0, sid="mean_reversion_swing_ibkr")])
    monkeypatch.setattr(TE, "_last_close", lambda s: 90.0)
    assert TE.evaluate("http://api.test", None)["strategies"] == []


def test_an_unreadable_order_book_says_so(monkeypatch):
    def _boom(*a, **k): raise RuntimeError("refused")
    monkeypatch.setattr("requests.get", _boom)
    ev = TE.evaluate("http://api.test", None)
    assert "could not read" in ev["error"]
    assert "unavailable" in TE.render(ev)


def test_no_signals_yet_is_explained_not_blank():
    out = TE.render({"as_of": "2026-09-02T13:00", "strategies": []})
    assert "No signals placed yet" in out
