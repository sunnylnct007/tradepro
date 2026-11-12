"""IBKR "Chart data unavailable" is a warm-up, not a network fault.

12 Nov 2026, first run with decline messages (#387): the first symbols of the
5m run — AAPL, ABBV, … — read
    ibkr_web declined (network): history fetch failed (attempt 3/3):
    {"error":"Chart data unavailable"}
and went to Yahoo, while the same conids served seconds later. The API's three
quick retries (1 s, 2 s) are inside the window the history farm needs; the
provider must wait a few seconds and ask again before giving up.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from tradepro_strategies.bar_cache.errors import BarFetchError, ProviderNetworkError
from tradepro_strategies.bar_cache.providers.ibkr_web_provider import IBKRWebProvider, is_chart_warming

UTC = timezone.utc
WARMING = 'history fetch failed for AAPL (conid 265598, attempt 3/3): {"error":"Chart data unavailable"}'


def _bars(n=3):
    t0 = int(datetime(2026, 11, 12, 14, 30, tzinfo=UTC).timestamp() * 1000)
    return {"bars": [{"t": t0 + i * 300_000, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1} for i in range(n)]}


@pytest.mark.parametrize("reason,expected", [
    (WARMING, True), ("Chart data unavailable", True),
    ("Too many history chart requests, please try again later.", False),
    ("no contract found", False), ("", False),
])
def test_warming_detection(reason, expected):
    assert is_chart_warming(reason) is expected


def test_a_warming_conid_is_asked_again_and_serves(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setenv("TRADEPRO_IBKR_WARMUP_BACKOFF", "5,10")
    replies = iter([(502, {"error": WARMING}), (200, _bars())])
    p = IBKRWebProvider(base="http://api.test", token="t", _get=lambda u, h, t: next(replies))

    df, _ = p.fetch("AAPL", "us_etf", "5m", datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 13, tzinfo=UTC))

    assert sleeps == [5]
    assert len(df) == 3 and set(df["source"]) == {"ibkr_web"}


def test_exhausted_warmup_is_its_own_class_not_network(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setenv("TRADEPRO_IBKR_WARMUP_BACKOFF", "1,1")
    p = IBKRWebProvider(base="http://api.test", token="t", _get=lambda u, h, t: (502, {"error": WARMING}))
    with pytest.raises(BarFetchError) as ei:
        p.fetch("AAPL", "us_etf", "5m", datetime(2026, 11, 12, tzinfo=UTC), datetime(2026, 11, 13, tzinfo=UTC))
    assert ei.value.error_class == "warmup"
    assert not isinstance(ei.value, ProviderNetworkError)
