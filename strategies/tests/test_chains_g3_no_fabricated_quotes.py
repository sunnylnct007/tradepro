"""Route-B fail-loud regression (8 Aug 2026 finding): fetch_chain_g3 must
never zero-fill quote-less legs into fabricated $0-premium / 0-IV quotes.

A leg with bid/ask/delta ALL null is the cold-quote-cache / market-closed
signature — it must be dropped; a chain where EVERY leg is like that must
come back as None (the module's "NO FALSE POSITIVES" contract), not as an
OptionChain that prices a CSP at zero yield.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tradepro_strategies.quant_engine.options import chains_g3


def _resp(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        json=lambda: payload,
        raise_for_status=lambda: None,
    )


def _mock_get(monkeypatch, chain_payload: dict) -> None:
    months_payload = {"months": ["SEP26"], "underlyingConId": 1, "error": None}

    def fake_get(url, **kwargs):
        if url.endswith("/months"):
            return _resp(months_payload)
        return _resp(chain_payload)

    monkeypatch.setattr(chains_g3.requests, "get", fake_get)


def _leg(strike, right="P", **quote):
    return {"strike": strike, "right": right,
            "bid": quote.get("bid"), "ask": quote.get("ask"),
            "delta": quote.get("delta"), "impliedVolPct": quote.get("iv"),
            "openInterest": quote.get("oi")}


class TestNoFabricatedQuotes:
    def test_all_cold_legs_returns_none(self, monkeypatch):
        _mock_get(monkeypatch, {
            "spot": 100.0, "error": None,
            "legs": [_leg(95), _leg(100), _leg(105)],
        })
        assert chains_g3.fetch_chain_g3(
            "SPY", api_base="http://test", api_token="t") is None

    def test_cold_legs_dropped_warm_legs_kept(self, monkeypatch):
        _mock_get(monkeypatch, {
            "spot": 100.0, "error": None,
            "legs": [
                _leg(95),                                  # cold — dropped
                _leg(100, bid=1.10, ask=1.20, delta=-0.30, iv=22.0, oi=500),
                _leg(105, ask=0.05),                       # ask-only — kept (real "no bid")
            ],
        })
        chain = chains_g3.fetch_chain_g3("SPY", api_base="http://test", api_token="t")
        assert chain is not None
        assert len(chain.puts) == 2
        strikes = {q.strike for q in chain.puts}
        assert strikes == {100.0, 105.0}
        at_100 = next(q for q in chain.puts if q.strike == 100.0)
        assert at_100.bid == pytest.approx(1.10)
        assert at_100.iv == pytest.approx(0.22)

    def test_backend_error_returns_none(self, monkeypatch):
        # The backend now sets error for the all-quote-less case — that path
        # must also come back None (was already true; pin it).
        _mock_get(monkeypatch, {
            "spot": 100.0,
            "error": "option quotes still cold after warm-up retry",
            "legs": [_leg(100, bid=1.0)],
        })
        assert chains_g3.fetch_chain_g3(
            "SPY", api_base="http://test", api_token="t") is None
