"""A name listed in July is not asked for every month before July, every run.

17 Nov 2026: SKHY (listed Jul 2026) cost the rotation lane five minutes and ~40
IBKR history calls per run — one refused cache-miss month at a time, each with
warm-up retries, until IBKR's pacing limit tripped for the names behind it.
Once the chain refuses a missing month older than the first stored month, the
months between are not asked for in that read. A month AFTER the first stored
one is always asked, and nothing is written for the refused months.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from tradepro_strategies.bar_cache import BarStore
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin
from tradepro_strategies.bar_cache.errors import ProviderNetworkError
from tradepro_strategies.bar_cache.providers import Provider, register_provider

UTC = timezone.utc
_PLUGIN = UsEtfPlugin()
_LISTED = datetime(2026, 7, 1, tzinfo=UTC)


def _bars(start, end):
    days = [d for d in _PLUGIN.expected_session_dates(start, end)
            if datetime(d.year, d.month, d.day, tzinfo=UTC) < end]
    idx = pd.DatetimeIndex([datetime(d.year, d.month, d.day, tzinfo=UTC) for d in days])
    n = len(idx)
    return pd.DataFrame({"open": [10.0]*n, "high": [10.0]*n, "low": [10.0]*n, "close": [10.0]*n,
                         "volume": [1000]*n, "adj_factor": [1.0]*n, "source": ["t"]*n}, index=idx)


class _ListedInJuly(Provider):
    def __init__(self, name):
        self.name, self.asked = name, []

    def fetch(self, canonical, asset_class, resolution, start, end):
        self.asked.append(start.strftime("%Y-%m"))
        if end <= _LISTED:
            raise ProviderNetworkError(provider=self.name, canonical=canonical,
                                       message='{"error":"Chart data unavailable"}')
        return _bars(max(start, _LISTED), end), {"provider_version": "t"}

    def max_history(self, resolution): return None


def _store(tmp_path, monkeypatch, request):
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_DISABLE", "1")
    prov = _ListedInJuly(f"pl_{request.node.name}"[:40])
    register_provider(prov)
    return BarStore(base_dir=tmp_path, provider_chain=[prov.name]), prov


def test_months_before_the_listing_are_asked_once_per_read(tmp_path, monkeypatch, request):
    store, prov = _store(tmp_path, monkeypatch, request)
    # seed the listed months the way a harvest would
    store.get(canonical="NEWCO", asset_class="us_etf", resolution="1d",
              start=_LISTED, end=datetime(2026, 9, 1, tzinfo=UTC), allow_partial=True)
    prov.asked.clear()

    bf = store.get(canonical="NEWCO", asset_class="us_etf", resolution="1d",
                   start=datetime(2025, 9, 1, tzinfo=UTC), end=datetime(2026, 8, 31, tzinfo=UTC),
                   allow_partial=True)

    assert prov.asked == ["2025-09"], prov.asked         # one refusal, not ten
    assert not bf.df.empty and bf.df.index.min() >= _LISTED
    assert store.stored_partitions("NEWCO", "us_etf", "1d")[0] == "2026-07"  # nothing written


def test_a_refusal_with_no_stored_history_does_not_skip_anything(tmp_path, monkeypatch, request):
    store, prov = _store(tmp_path, monkeypatch, request)
    store.get(canonical="NEWCO", asset_class="us_etf", resolution="1d",
              start=datetime(2026, 4, 1, tzinfo=UTC), end=datetime(2026, 8, 31, tzinfo=UTC),
              allow_partial=True)
    # nothing was stored when April was refused, so May and June were still asked
    assert prov.asked[:3] == ["2026-04", "2026-05", "2026-06"]
