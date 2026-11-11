"""An INTRADAY delta fetch must include the session in progress.

THE DEFECT, 11 Nov 2026. The 5m lane runs every 30 minutes through the US
session. Three runs that day (16:26Z, 16:58Z, 19:07Z) each reported

    Done: 109 complete  7 partial  0 failed  / 116 symbols   source=cache

and not one of the 116 symbols held a single bar for 11 Nov. Reproduced on
SPY alone with a window covering yesterday AND today: "78/78 bars, gold,
source=cache" — 78 bars is exactly one session.

THE CAUSE. The delta-mode block in `store.get` capped the fetch window at
`_last_settled_session` for EVERY resolution. The clamp on the non-delta path,
written the same week (1 Sep 2026), carried `_is_daily_or_coarser(resolution)`
because "an intraday lane legitimately wants today's partial bars — that is the
whole point of a 5m harvest". The delta block never got that exemption, so on
a cache that already held yesterday, today's session was the delta, the cap
reduced it to nothing, and the run was logged `delta_unsettled_skip` and
graded complete. Same file, two write sites, half an idea applied.

These tests drive `BarStore.get` end to end with a recording provider and a
frozen clock — not a source grep — so they fail on the unpatched store.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tradepro_strategies.bar_cache import BarStore
from tradepro_strategies.bar_cache import store as store_mod
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin
from tradepro_strategies.bar_cache.providers import Provider, register_provider

UTC = timezone.utc
_PLUGIN = UsEtfPlugin()

# November 2026: EST, so the US session is 14:30–21:00 UTC. Mon 9, Tue 10, Wed 11.
_MON = datetime(2026, 11, 9, tzinfo=UTC)
_WED = datetime(2026, 11, 11, tzinfo=UTC)
_THU = datetime(2026, 11, 12, tzinfo=UTC)
_NOW = datetime(2026, 11, 11, 15, 40, tzinfo=UTC)      # 70 min into Wednesday
_WED_BARS_SO_FAR = 14                                   # 14:30 … 15:35 inclusive


def _session_bars(d, resolution):
    if resolution == "1d":
        return [datetime(d.year, d.month, d.day, tzinfo=UTC)]
    open_ = datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)
    return [open_ + timedelta(minutes=5 * k) for k in range(78)]


def _bars_for(start, end, *, resolution, source):
    ts = [
        t for d in _PLUGIN.expected_session_dates(start, end)
        for t in _session_bars(d, resolution)
        if start <= t < end
    ]
    idx = pd.DatetimeIndex(ts)
    n = len(idx)
    return pd.DataFrame(
        {"open": [100.0] * n, "high": [100.0] * n, "low": [100.0] * n,
         "close": [100.0] * n, "volume": [1000] * n,
         "adj_factor": [1.0] * n, "source": [source] * n},
        index=idx,
    )


class _RecordingProvider(Provider):
    def __init__(self, name):
        self.name = name
        self.calls = []
        self.cutoff = _WED          # "bars have printed up to <cutoff>"

    def fetch(self, canonical, asset_class, resolution, start, end):
        self.calls.append((start, end))
        df = _bars_for(start, min(end, self.cutoff), resolution=resolution,
                       source=self.name)
        return df, {"provider_version": "test", "rows": len(df)}

    def max_history(self, resolution):
        return None


class _Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return _NOW if tz is None else _NOW.astimezone(tz)


@pytest.fixture()
def store_and_provider(tmp_path, monkeypatch, request):
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_DISABLE", "1")
    monkeypatch.setattr(store_mod, "datetime", _Frozen)
    provider = _RecordingProvider(f"faketest_{request.node.name}"[:60])
    register_provider(provider)
    store = BarStore(base_dir=tmp_path, provider_chain=[provider.name])
    return store, provider


def _get(store, resolution):
    return store.get(canonical="SPY", asset_class="us_etf", resolution=resolution,
                     start=_MON, end=_THU, allow_partial=True)


def _prime_through_tuesday(store, provider, resolution):
    """First call: a cold cache, Tuesday's close is the last printed bar."""
    provider.cutoff = _WED
    _get(store, resolution)
    assert len(provider.calls) == 1
    provider.cutoff = _NOW          # Wednesday is now 70 minutes old


def test_5m_delta_asks_for_the_session_in_progress(store_and_provider):
    store, provider = store_and_provider
    _prime_through_tuesday(store, provider, "5m")
    _get(store, "5m")
    assert len(provider.calls) == 2, (
        "no delta fetch at all — today's session was treated as unreachable "
        f"(calls={provider.calls})")
    _, delta_end = provider.calls[1]
    assert delta_end > _WED, (
        f"delta window capped at {delta_end:%Y-%m-%d %H:%M}: the open session "
        "was clamped away, exactly the 11 Nov defect")


def test_5m_frame_carries_todays_printed_bars(store_and_provider):
    store, provider = store_and_provider
    _prime_through_tuesday(store, provider, "5m")
    bf = _get(store, "5m")
    df = bf.df if hasattr(bf, "df") else bf
    today = df[df.index.tz_convert("UTC").normalize() == _WED]
    assert len(today) == _WED_BARS_SO_FAR, (
        f"{len(today)} bars for today; expected the {_WED_BARS_SO_FAR} that had "
        "printed by 15:40Z. The lane ran three times on 11 Nov and wrote none.")


def test_daily_delta_still_stops_at_the_last_settled_session(store_and_provider):
    """The 1 Sep guard is untouched: no daily bar for an open session."""
    store, provider = store_and_provider
    _prime_through_tuesday(store, provider, "1d")
    bf = _get(store, "1d")
    df = bf.df if hasattr(bf, "df") else bf
    assert not (df.index.tz_convert("UTC").normalize() == _WED).any(), (
        "a DAILY bar for the session in progress was written — the 1 Sep "
        "contamination is back")
    for _, end in provider.calls[1:]:
        assert end <= _WED, f"daily delta asked past the settled close: {end}"
