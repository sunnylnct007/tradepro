"""IBKR's history PACING reply must take the backoff path, not the Yahoo path.

12 Nov 2026, 14:00Z run: 104 of 116 symbols reported `ibkr_web_network` and were
written from yfinance while the IBKR session was connected and quotes were
live. The API had answered 502 with IBKR's exact text — "Too many history
chart requests, please try again later." — and `_RATE_LIMIT_MARKERS` held
"too many requests", which that sentence does not contain. The rate-limit
branch (15s/45s/120s/300s waits) never ran; the generic branch raised a
NETWORK error and the chain fell through. The provider's docstring called
`is_rate_limited` "unit-tested"; no test existed.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from tradepro_strategies.bar_cache.errors import ProviderNetworkError, ProviderRateLimitError
from tradepro_strategies.bar_cache.providers.ibkr_web_provider import IBKRWebProvider, is_rate_limited

IBKR_TEXT = "Too many history chart requests, please try again later."
API_WRAPPED = f'history fetch failed for XLU (conid 4215235, attempt 3/3): {{"error":"{IBKR_TEXT}"}}'
UTC = timezone.utc


@pytest.mark.parametrize("status,reason", [
    (502, API_WRAPPED),                 # what the API actually returned on 12 Nov
    (429, IBKR_TEXT),
    (429, ""),
    (400, "Too many requests"),
    (500, "pacing violation"),
])
def test_pacing_replies_are_rate_limited(status, reason):
    assert is_rate_limited(status, reason)


@pytest.mark.parametrize("status,reason", [
    (502, "Chart data unavailable"),
    (503, "Service Unavailable"),
    (404, "no contract found"),
    (502, "HTTP 502"),
])
def test_other_failures_are_not_rate_limited(status, reason):
    assert not is_rate_limited(status, reason)


def _bars_payload(n=3):
    t0 = int(datetime(2026, 11, 12, 14, 30, tzinfo=UTC).timestamp() * 1000)
    return {"bars": [{"t": t0 + i * 300_000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 10} for i in range(n)]}


def test_a_paced_symbol_waits_and_then_gets_ibkr_bars(monkeypatch):
    """Two pacing replies, then bars. The provider must sleep and retry, and
    the bars must come back as ibkr_web — not a NetworkError to Yahoo."""
    replies = iter([(502, {"error": API_WRAPPED}), (429, {"error": IBKR_TEXT}), (200, _bars_payload())])
    calls, sleeps = [], []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))   # the provider does `import time as _time` inside the function
    monkeypatch.setenv("TRADEPRO_IBKR_RATELIMIT_BACKOFF", "1,2,3")

    def fake_get(url, headers, timeout):
        calls.append(url)
        return next(replies)

    p = IBKRWebProvider(base="http://api.test", token="t", _get=fake_get)
    df, meta = p.fetch("XLU", "us_etf", "5m",
                       datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 13, tzinfo=UTC))

    assert len(calls) == 3, calls
    assert sleeps == [1, 2], "backoff waits must come from the schedule, in order"
    assert len(df) == 3 and set(df["source"]) == {"ibkr_web"}


def test_exhausted_backoff_is_a_rate_limit_error_not_a_network_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setenv("TRADEPRO_IBKR_RATELIMIT_BACKOFF", "1,1")
    p = IBKRWebProvider(base="http://api.test", token="t",
                        _get=lambda url, headers, timeout: (429, {"error": IBKR_TEXT}))
    with pytest.raises(ProviderRateLimitError) as ei:
        p.fetch("XLU", "us_etf", "5m",
                datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 13, tzinfo=UTC))
    assert "RATE LIMITED after 2" in str(ei.value)
    assert not isinstance(ei.value, ProviderNetworkError)
