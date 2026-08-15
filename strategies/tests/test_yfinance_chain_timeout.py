"""The yfinance fallback must be time-bounded and must never fail silently.

15 Aug 2026: a single day's options-screen run took 2h50m. The cause was not
logic — it was that 53 of 82 symbols fall through to yfinance for their option
chain, Yahoo was rate-limiting the machine (`YFRateLimitError: Too Many
Requests`), and `fetch_chain` had NO timeout, NO retry, NO backoff and a bare
`except Exception: return None`. Each throttled symbol blocked on a socket read
with no deadline, and nothing anywhere said why.

A fallback with no time bound is not a fallback, it is a hang. These tests pin
both halves: the bound, and the loudness.
"""
from __future__ import annotations

import logging

import pytest

from tradepro_strategies.quant_engine.options import chains as C


class _Boom:
    def __init__(self, exc):
        self._exc = exc

    def Ticker(self, *a, **k):
        raise self._exc


class TestFailsLoudly:
    def test_a_failure_is_logged_not_swallowed(self, monkeypatch, caplog):
        """The original `except Exception: return None` is why three-hour runs
        and a 65%-Yahoo board went unnoticed for weeks."""
        monkeypatch.setattr(C, "_yf_session", lambda: None)
        import sys
        monkeypatch.setitem(sys.modules, "yfinance", _Boom(RuntimeError("network is down")))
        with caplog.at_level(logging.WARNING):
            assert C.fetch_chain("XOM") is None
        assert "XOM" in caplog.text
        assert "network is down" in caplog.text
        assert "FAILED" in caplog.text

    def test_rate_limiting_is_named_as_such(self, monkeypatch, caplog):
        """"Yahoo refused to answer us" and "this symbol has no chain" must
        never read the same on a trading board."""
        class YFRateLimitError(Exception):
            pass

        import sys
        monkeypatch.setattr(C, "_yf_session", lambda: None)
        monkeypatch.setitem(sys.modules, "yfinance",
                            _Boom(YFRateLimitError("Too Many Requests. Rate limited.")))
        with caplog.at_level(logging.WARNING):
            assert C.fetch_chain("KO") is None
        assert "RATE-LIMITED" in caplog.text
        assert "not the market being closed" in caplog.text

    def test_a_plain_failure_is_not_mislabelled_as_rate_limiting(self, monkeypatch, caplog):
        import sys
        monkeypatch.setattr(C, "_yf_session", lambda: None)
        monkeypatch.setitem(sys.modules, "yfinance", _Boom(ValueError("delisted")))
        with caplog.at_level(logging.WARNING):
            C.fetch_chain("DEAD")
        assert "RATE-LIMITED" not in caplog.text


class TestTimeoutIsReal:
    def test_the_session_carries_a_timeout(self):
        """Not a mock: the real session object must actually hold a deadline."""
        C._YF_SESSION = None
        s = C._yf_session()
        assert s is not None, "no session means no timeout means the hang is back"
        # curl_cffi stores it as .timeout; the requests fallback binds it into
        # a functools.partial on .request.
        bound = getattr(s, "timeout", None)
        if bound is None:
            bound = getattr(getattr(s, "request", None), "keywords", {}).get("timeout")
        assert bound == C._YF_TIMEOUT_S

    def test_the_budget_is_configurable_not_hardcoded(self, monkeypatch):
        import importlib
        monkeypatch.setenv("TRADEPRO_YF_TIMEOUT_S", "3")
        mod = importlib.reload(C)
        try:
            assert mod._YF_TIMEOUT_S == 3.0
        finally:
            monkeypatch.delenv("TRADEPRO_YF_TIMEOUT_S", raising=False)
            importlib.reload(C)

    def test_default_budget_is_bounded_and_sane(self):
        assert 0 < C._YF_TIMEOUT_S <= 30, (
            "an unbounded or huge budget reintroduces the 2h50m run")

    def test_session_is_reused_not_rebuilt_per_symbol(self):
        C._YF_SESSION = None
        assert C._yf_session() is C._yf_session()
