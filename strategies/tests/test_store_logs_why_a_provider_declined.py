"""A declined provider must leave its MESSAGE behind, not just its class.

12 Nov 2026: the 16:00Z lane run reported `23 × ibkr_web_network`. The chain
log, the event telemetry and the run summary all carried that token and
nothing else; the provider's actual error text was never written anywhere a
person at the desk could read. This pins: the store logs
"<symbol> <res>: <provider> declined (<class>): <message>" and keeps one sample
per reason for the harvest summary.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd
import pytest

from tradepro_strategies.bar_cache import BarStore
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin
from tradepro_strategies.bar_cache.errors import ProviderNetworkError
from tradepro_strategies.bar_cache.providers import Provider, register_provider

UTC = timezone.utc
_PLUGIN = UsEtfPlugin()
_AUG4, _AUG8 = datetime(2025, 8, 4, tzinfo=UTC), datetime(2025, 8, 8, tzinfo=UTC)


def _bars_for(start, end, source):
    days = [d for d in _PLUGIN.expected_session_dates(start, end)
            if datetime(d.year, d.month, d.day, tzinfo=UTC) < end]
    idx = pd.DatetimeIndex([datetime(d.year, d.month, d.day, tzinfo=UTC) for d in days])
    n = len(idx)
    return pd.DataFrame({"open": [100.0]*n, "high": [100.0]*n, "low": [100.0]*n, "close": [100.0]*n,
                         "volume": [1000]*n, "adj_factor": [1.0]*n, "source": [source]*n}, index=idx)


class _Refusing(Provider):
    def __init__(self, name): self.name = name
    def fetch(self, canonical, asset_class, resolution, start, end):
        raise ProviderNetworkError(provider=self.name, canonical=canonical,
                                   message="ibkr_web request failed: connection refused by api:8081")
    def max_history(self, resolution): return None


class _Serving(Provider):
    def __init__(self, name): self.name = name
    def fetch(self, canonical, asset_class, resolution, start, end):
        return _bars_for(start, min(end, _AUG8), self.name), {"provider_version": "t", "rows": 4}
    def max_history(self, resolution): return None


def test_the_declined_providers_message_is_logged_and_sampled(tmp_path, monkeypatch, caplog, request):
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_DISABLE", "1")
    tag = f"ft_{request.node.name}"[:40]
    first, second = _Refusing(f"{tag}_ibkr"), _Serving(f"{tag}_yf")
    register_provider(first); register_provider(second)
    store = BarStore(base_dir=tmp_path, provider_chain=[first.name, second.name])
    BarStore.last_decline_messages.clear()

    with caplog.at_level(logging.WARNING, logger="tradepro.bar_cache.store"):
        bf = store.get(canonical="SPY", asset_class="us_etf", resolution="1d",
                       start=_AUG4, end=_AUG8, allow_partial=True)

    assert bf.provider_used.startswith(second.name)
    line = next((r.getMessage() for r in caplog.records if "declined" in r.getMessage()), None)
    assert line is not None, caplog.text
    assert f"SPY 1d: {first.name} declined (network): ibkr_web request failed: connection refused by api:8081" in line
    assert BarStore.last_decline_messages[f"{first.name}_network"] == \
        "SPY: ibkr_web request failed: connection refused by api:8081"
