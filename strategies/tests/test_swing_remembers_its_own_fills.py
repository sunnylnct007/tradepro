"""The strategy must remember the positions IT opened, across a restart.

18 Sep 2026. The paper daemon re-executes every 900 seconds. `_fill_price` and
`_entry_bar` were plain in-memory dicts, and remember()/recall() write to
`_state` — a bare dict the engine never snapshots, despite a comment claiming
it persists. So every restart forgot its own fills, every held position fell
through to `ignore-inherited`, and the exit path never ran once:

    4,886 ignore-inherited · 134 entry · 0 hold · 0 exit

The book could only accumulate. Winners never reached their target (COP
+11.8%, LRCX +5.6%); losers never hit their stop — ARWR traded 12% THROUGH a
stop that should have closed it at -6.7%, and was marked -18.1%.

Ownership now comes from the OMS, which outlives the process. The safety
property that motivated the original design is unchanged and tested here: a
position is ours only if WE filled it.
"""
import pytest
import requests

from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy,
)

SID = "mean_reversion_swing_ibkr"


def _order(sym, side, state, px, when, sid=SID):
    return {"symbol": f"{sym}_US_EQ", "side": side, "state": state,
            "avgFillPrice": px, "createdAtUtc": when, "strategyId": sid}


class _Bare(MeanReversionSwingStrategy):
    """Only the fields _seed_from_oms touches — this is a unit, not a session."""

    def __init__(self):
        self.strategy_id = SID
        self._fill_price = {}
        self._entry_bar = {}


@pytest.fixture
def seed(monkeypatch):
    def _run(orders):
        class _R:
            status_code = 200

            @staticmethod
            def json():
                return orders

        monkeypatch.setattr(requests, "get", lambda *a, **k: _R())
        monkeypatch.setattr(
            "tradepro_strategies.cli.push_to_api.load_credentials",
            lambda: ("https://example.invalid", "tok"))
        s = _Bare()
        MeanReversionSwingStrategy._seed_from_oms(s)
        return s
    return _run


def test_it_claims_a_position_this_strategy_filled(seed):
    s = seed([_order("ARWR", "BUY", "FILLED", 81.22, "2026-09-01T14:00:00Z")])
    assert s._fill_price["ARWR"] == 81.22
    assert s._entry_bar["ARWR"] == "2026-09-01"


def test_it_never_claims_another_strategys_position(seed):
    """The original bug this design existed to prevent — still prevented."""
    s = seed([_order("DIS", "BUY", "FILLED", 100.0, "2026-09-01T14:00:00Z",
                     sid="ichimoku_equity")])
    assert s._fill_price == {} and s._entry_bar == {}


def test_an_unfilled_order_is_not_a_position(seed):
    s = seed([_order("GS", "BUY", "CANCELLED", None, "2026-09-02T14:00:00Z"),
              _order("GS", "BUY", "REJECTED", None, "2026-09-02T15:00:00Z")])
    assert s._fill_price == {}


def test_a_later_sell_releases_the_position(seed):
    s = seed([_order("COP", "BUY", "FILLED", 120.0, "2026-09-01T14:00:00Z"),
              _order("COP", "SELL", "FILLED", 131.9, "2026-09-10T14:00:00Z")])
    assert "COP" not in s._fill_price


def test_order_of_arrival_does_not_matter(seed):
    """Records come back newest-first; the buy must not erase its own sell."""
    s = seed([_order("COP", "SELL", "FILLED", 131.9, "2026-09-10T14:00:00Z"),
              _order("COP", "BUY", "FILLED", 120.0, "2026-09-01T14:00:00Z")])
    assert "COP" not in s._fill_price


def test_a_rebought_symbol_is_managed_again(seed):
    s = seed([_order("BAC", "BUY", "FILLED", 59.06, "2026-09-01T14:00:00Z"),
              _order("BAC", "SELL", "FILLED", 62.37, "2026-09-05T14:00:00Z"),
              _order("BAC", "BUY", "FILLED", 57.10, "2026-09-15T14:00:00Z")])
    assert s._fill_price["BAC"] == 57.10
    assert s._entry_bar["BAC"] == "2026-09-15"


def test_a_zero_or_missing_fill_price_is_not_trusted(seed):
    s = seed([_order("MS", "BUY", "FILLED", 0, "2026-09-01T14:00:00Z"),
              _order("SWK", "BUY", "FILLED", None, "2026-09-01T14:00:00Z")])
    assert s._fill_price == {}


def test_an_unreachable_oms_manages_NOTHING_rather_than_guessing(monkeypatch):
    """Fails closed: no fills means no exits, never an exit on a guessed basis."""
    def _boom(*a, **k):
        raise ConnectionError("oms down")
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("https://example.invalid", "tok"))
    s = _Bare()
    MeanReversionSwingStrategy._seed_from_oms(s)
    assert s._fill_price == {}
