"""The broker read must ask the broker the sleeve RUNS ON.

10 Nov 2026. The T212 momentum lane logged, on every run since 8 Nov:

    dropped 29 position(s) the OMS claims and the BROKER does not hold: AES, AMG, ...
    seeded 1 open fill(s) confirmed against the broker

T212 held all 30. The read was hard-coded to /integrations/ibkr/positions, so the
lane asked IBKR what T212 held; the one survivor was a name the IBKR momentum
sleeve also held. A closer that believes the book is empty closes nothing.
"""
from tradepro_strategies.paper.strategies.mean_reversion_swing import (
    MeanReversionSwingStrategy)


class _Resp:
    status_code = 200

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return {"positions": self._rows}


def _capture(monkeypatch, rows):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {})))
        return _Resp(rows)

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        "tradepro_strategies.cli.push_to_api.load_credentials",
        lambda: ("http://api.test", "tok"))
    return calls


def test_a_t212_sleeve_asks_t212_not_ibkr(monkeypatch):
    calls = _capture(monkeypatch, [{"ticker": "AES_US_EQ", "quantity": 66.0}])
    s = MeanReversionSwingStrategy(strategy_id="momentum_pullback_t212",
                                   params={"broker": "t212"})
    assert s._broker_positions() == {"AES": 66.0}
    assert len(calls) == 1 and "trading212/positions" in calls[0][0]
    assert "ibkr" not in calls[0][0]


def test_an_ibkr_sleeve_still_forces_a_fresh_ibkr_read(monkeypatch):
    calls = _capture(monkeypatch, [{"symbol": "TECH", "position": 41}])
    s = MeanReversionSwingStrategy(strategy_id="momentum_pullback_ibkr",
                                   params={"broker": "ibkr"})
    assert s._broker_positions() == {"TECH": 41.0}
    assert "integrations/ibkr/positions" in calls[0][0]
    assert calls[0][1].get("fresh") == "true"


def test_no_broker_param_means_ibkr_the_historical_default(monkeypatch):
    calls = _capture(monkeypatch, [{"symbol": "TECH", "position": 1}])
    MeanReversionSwingStrategy(strategy_id="swing_ibkr")._broker_positions()
    assert "integrations/ibkr/positions" in calls[0][0]


def test_an_unsupported_broker_is_unreadable_not_empty(monkeypatch):
    calls = _capture(monkeypatch, [])
    s = MeanReversionSwingStrategy(strategy_id="x", params={"broker": "ig"})
    assert s._broker_positions() is None
    assert calls == []


def test_the_paper_session_hands_the_swing_family_its_broker():
    import inspect
    from tradepro_strategies.cli import paper_session as ps
    src = inspect.getsource(ps)
    i = src.index("if strategy_name in _SWING_FAMILY:")
    assert '"broker": args.broker' in src[i:i + 1500], (
        "the swing/momentum construction must pass the broker, or the broker "
        "read has nothing to route on and asks IBKR what T212 holds")
