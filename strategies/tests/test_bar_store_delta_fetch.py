"""Regression coverage for the BarStore DELTA fetch + merge write (21 Aug 2026).

Before this, an incomplete month partition (every live month, by definition)
was re-fetched over its WHOLE window on every call — 5×7-day provider slices
per partition per symbol per harvest run — and the write path was
replace-not-merge, so a rate-limited partial answer hit the shrink guard,
nothing was written, and the next run re-pulled the whole month again. The
5-minute harvest lane never completed once in its life because of this loop.

Now: only the absent sessions are requested (delta window), the answer is
MERGED into the cached partition (prefer-existing, so cached provenance is
never downgraded), and an all-providers-empty delta is a quiet no-op rather
than a loud no_provider failure.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from tradepro_strategies.bar_cache import BarFetchError, BarStore
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin
from tradepro_strategies.bar_cache.providers import Provider, register_provider

_PLUGIN = UsEtfPlugin()

# A fixed, fully-in-the-past month so the calendar is deterministic.
_AUG4 = datetime(2025, 8, 4, tzinfo=UTC)    # Monday
_AUG7 = datetime(2025, 8, 7, tzinfo=UTC)
_AUG8 = datetime(2025, 8, 8, tzinfo=UTC)


def _bars_for(start: datetime, end: datetime, *, source: str,
              close: float = 100.0) -> pd.DataFrame:
    """One 1d bar per expected session in [start, end) — end-EXCLUSIVE,
    like a real provider honouring the half-open request window
    (the plugin helper itself is end-inclusive)."""
    sessions = [
        d for d in _PLUGIN.expected_session_dates(start, end)
        if datetime(d.year, d.month, d.day, tzinfo=UTC) < end
    ]
    idx = pd.DatetimeIndex(
        [datetime(d.year, d.month, d.day, tzinfo=UTC) for d in sessions],
    )
    n = len(idx)
    return pd.DataFrame(
        {"open": [close] * n, "high": [close] * n, "low": [close] * n,
         "close": [close] * n, "volume": [1000] * n,
         "adj_factor": [1.0] * n, "source": [source] * n},
        index=idx,
    )


class _RecordingProvider(Provider):
    """Serves scripted frames and records every requested window."""

    def __init__(self, name: str):
        self.name = name
        self.calls: list[tuple[datetime, datetime]] = []
        # Simulates "the market has only produced bars up to <cutoff>":
        # a real provider asked for a whole live month serves only the
        # sessions that have happened. Tests move the cutoff forward.
        self.cutoff = _AUG7
        self.responder = lambda start, end: _bars_for(
            start, min(end, self.cutoff), source=self.name,
        )

    def fetch(self, canonical, asset_class, resolution, start, end):
        self.calls.append((start, end))
        df = self.responder(start, end)
        return df, {"provider_version": "test", "rows": len(df)}

    def max_history(self, resolution):
        return None


@pytest.fixture()
def store_and_provider(tmp_path, monkeypatch, request):
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    provider = _RecordingProvider(f"faketest_{request.node.name}"[:60])
    register_provider(provider)
    store = BarStore(base_dir=tmp_path, provider_chain=[provider.name])
    return store, provider


def _get(store, start, end, **kw):
    return store.get(
        canonical="SPY", asset_class="us_etf", resolution="1d",
        start=start, end=end, allow_partial=True, **kw,
    )


def test_second_fetch_is_delta_not_whole_partition(store_and_provider):
    store, provider = store_and_provider

    _get(store, _AUG4, _AUG7)          # seeds Aug 4-6
    assert len(provider.calls) == 1
    # Cache miss: full partition window is correct on first contact.
    assert provider.calls[0][0] == datetime(2025, 8, 1, tzinfo=UTC)

    provider.cutoff = _AUG8             # Aug 7's session has now happened
    result = _get(store, _AUG4, _AUG8)  # asks for one more session (Aug 7)
    assert len(provider.calls) == 2
    delta_start, delta_end = provider.calls[1]
    # The second request must cover ONLY the absent session, not the month.
    assert delta_start == _AUG7
    assert delta_end == _AUG8
    # Merged read: original 3 sessions + the new one.
    assert len(result.df) == 4


def test_covered_range_is_a_cache_hit_even_on_incomplete_month(
        store_and_provider):
    store, provider = store_and_provider
    _get(store, _AUG4, _AUG7)
    _get(store, _AUG4, _AUG7)   # identical ask — everything is on disk
    assert len(provider.calls) == 1


def test_empty_delta_answer_is_quiet_and_keeps_cache(store_and_provider):
    store, provider = store_and_provider
    _get(store, _AUG4, _AUG7)

    result = _get(store, _AUG4, _AUG8)   # needs Aug 7; cutoff still Aug 7 → empty
    # No no_provider blow-up; the cached three sessions still come back.
    assert len(result.df) == 3
    assert result.coverage_complete is False


def test_merge_prefers_existing_rows_so_provenance_survives(
        store_and_provider):
    store, provider = store_and_provider
    provider.responder = lambda start, end: _bars_for(
        start, min(end, provider.cutoff), source="golden", close=100.0,
    )
    _get(store, _AUG4, _AUG7)

    # The delta answer overlaps a cached session with DIFFERENT data.
    provider.responder = lambda start, end: _bars_for(
        datetime(2025, 8, 6, tzinfo=UTC), _AUG8,
        source="fallback", close=55.0,
    )
    result = _get(store, _AUG4, _AUG8)

    aug6 = result.df.loc[datetime(2025, 8, 6, tzinfo=UTC)]
    assert aug6["source"] == "golden"     # cached row untouched
    assert aug6["close"] == 100.0
    aug7 = result.df.loc[datetime(2025, 8, 7, tzinfo=UTC)]
    assert aug7["source"] == "fallback"   # new session added


def test_force_refresh_validated_shrink_replaces_phantom_rows(
        store_and_provider):
    """22 Aug 2026: wrong-venue contracts printed bars on US-closed days, so
    poisoned months held MORE rows than truth and the shrink guard kept the
    poison immortal. A force_refresh whose frame covers every expected
    session may replace a larger cached partition."""
    store, provider = store_and_provider

    def _with_phantom(start, end):
        df = _bars_for(start, min(end, provider.cutoff), source="poison")
        # An extra bar on a SUNDAY — a date no US session calendar contains.
        phantom_ts = datetime(2025, 8, 3, tzinfo=UTC)
        phantom = df.iloc[[0]].copy()
        phantom.index = pd.DatetimeIndex([phantom_ts])
        return pd.concat([phantom, df]).sort_index()

    provider.responder = _with_phantom
    provider.cutoff = datetime(2025, 9, 1, tzinfo=UTC)   # serve FULL months
    seeded = _get(store, _AUG4, _AUG8)
    assert len(seeded.df) >= 4          # phantom row is on disk

    # Truthful re-source: one FEWER row (no phantom), but every expected
    # session of the month covered — the validated-shrink case.
    provider.responder = lambda start, end: _bars_for(
        start, min(end, provider.cutoff), source="truth")
    fixed = _get(store, _AUG4, _AUG8, force_refresh=True)
    assert datetime(2025, 8, 3, tzinfo=UTC) not in fixed.df.index
    assert set(fixed.df["source"]) == {"truth"}

    # WITHOUT force_refresh a smaller answer still can't shrink the cache —
    # the guard's original job (rate-limited partials) is intact.
    provider.responder = lambda start, end: _bars_for(
        _AUG7, min(end, provider.cutoff), source="partial")
    read = _get(store, _AUG4, _AUG8)
    assert set(read.df["source"]) == {"truth"}


def test_provider_outage_serves_cached_rows_when_partial_allowed(
        store_and_provider):
    """21 Aug 2026: IBKR session dark + Yahoo rate-limited made every read
    raise no_provider while months of bars sat readable on disk."""
    store, provider = store_and_provider
    _get(store, _AUG4, _AUG7)   # cache Aug 4-6

    def _outage(start, end):
        raise BarFetchError(
            error_class="network", provider=provider.name,
            canonical="SPY", message="simulated outage",
            retry_strategy="retry_later",
        )
    provider.responder = _outage

    result = _get(store, _AUG4, _AUG8)   # needs Aug 7 → fetch fails
    assert len(result.df) == 3           # cached sessions still served
    assert result.coverage_complete is False

    # Without allow_partial the outage stays loud.
    with pytest.raises(BarFetchError):
        store.get(
            canonical="SPY", asset_class="us_etf", resolution="1d",
            start=_AUG4, end=_AUG8, allow_partial=False,
        )


def test_skip_fetch_serves_disk_without_touching_providers(
        store_and_provider):
    """Circuit-breaker mode: skip_fetch=True must contact NO provider and
    serve whatever is cached, flagged partial."""
    store, provider = store_and_provider
    _get(store, _AUG4, _AUG7)   # cache Aug 4-6
    calls_before = len(provider.calls)

    result = _get(store, _AUG4, _AUG8, skip_fetch=True)  # Aug 7 absent
    assert len(provider.calls) == calls_before           # no provider contact
    assert len(result.df) == 3
    assert result.coverage_complete is False
    assert "fetch_skipped" in result.provider_chain_tried


def test_fully_cached_prior_month_not_refetched_on_straddling_range(
        store_and_provider):
    store, provider = store_and_provider
    jul = datetime(2025, 7, 1, tzinfo=UTC)
    aug = datetime(2025, 8, 1, tzinfo=UTC)
    _get(store, jul, aug)                 # July complete
    provider.calls.clear()

    _get(store, datetime(2025, 7, 21, tzinfo=UTC), _AUG7)
    # Only the August partition may be fetched; a re-pull of July would
    # resurrect the cross-month double-fetch this change removed.
    assert all(call[0] >= aug for call in provider.calls)
