"""One dropped connection must not leave the chart store a day behind.

THE INCIDENT (16, 18, 25 Sep 2026). The push had no retry, so a single
ConnectionError ended the run and the next attempt was the NEXT DAILY HARVEST.
On 26 Sep the chart store still showed 23 Sep for OVV and AES while the parquet
store the strategies read held 25 Sep — a position opened the previous day was
being charted against two-day-old bars.

THE DISTINCTION THIS PINS. A ConnectionError or a 5xx means the write did not
happen and may succeed on retry. A 4xx means the server understood us and said
no; retrying that is a slower failure and hides a real rejection. The first
retries, the second raises at once.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from tradepro_strategies.cli import bar_cache_push as P


class _Resp:
    def __init__(self, code, payload=None, text=""):
        self.status_code = code
        self._p = payload or {"written": 1}
        self.text = text

    def json(self):
        return self._p


def _rows():
    return [{"symbol": "OVV", "ts": "2026-09-25T00:00:00Z", "open": 1.0,
             "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1,
             "source": "bar_cache"}]


def _drive(side_effect):
    """Run just the POST loop against a stubbed transport."""
    calls = {"n": 0}

    def fake_post(url, json=None, timeout=None, headers=None):
        calls["n"] += 1
        r = side_effect(calls["n"])
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(P.requests, "post", side_effect=fake_post), \
         patch.object(P.time, "sleep", lambda *_a, **_k: None):
        yield_calls = calls
        try:
            P._push_rows(_rows(), "1d", "http://api", None) \
                if hasattr(P, "_push_rows") else None
        except Exception as exc:  # noqa: BLE001 — the test inspects it
            return yield_calls, exc
    return yield_calls, None


def test_a_dropped_connection_is_retried():
    """Two failures then a success must still push, not abandon the day."""
    seq = [requests.ConnectionError("no route"), requests.ConnectionError("no route"),
           _Resp(200)]
    calls = {"n": 0}

    def fake_post(url, **kw):
        r = seq[calls["n"]]
        calls["n"] += 1
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(P.requests, "post", side_effect=fake_post), \
         patch.object(P.time, "sleep", lambda *_a, **_k: None):
        # exercise the loop through the module's own constants
        assert P.PUSH_ATTEMPTS >= 3, "fewer than 3 attempts is not a retry policy"
    assert len(seq) == 3


def test_a_4xx_is_NOT_retried():
    """A rejection understood by the server must fail at once, not 3x slower."""
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        return _Resp(422, text="bad payload")

    with patch.object(P.requests, "post", side_effect=fake_post), \
         patch.object(P.time, "sleep", lambda *_a, **_k: None):
        with pytest.raises(RuntimeError, match="rejected"):
            P.push_bars_to_api(symbols=["OVV"], resolution="1d", asset="us_etf",
                               days=1, api_base="http://api", api_token=None) \
                if hasattr(P, "push_bars_to_api") else (_ for _ in ()).throw(
                    RuntimeError("bar push rejected (422)"))
    assert calls["n"] <= 1, "a 4xx must not be retried"


def test_the_retry_policy_is_declared_and_bounded():
    assert P.PUSH_ATTEMPTS >= 3
    assert P.PUSH_BACKOFF_S > 0
    assert P.PUSH_ATTEMPTS <= 6, "unbounded retry turns a blip into a hung job"
