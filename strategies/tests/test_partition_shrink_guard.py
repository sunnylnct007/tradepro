"""A partition must never silently SHRINK.

The write path is replace-not-merge, and `_fetch_and_write` only guarded the
totally-empty response. A provider that returns a PARTIAL month — rate
limited, throttled, half-served — sailed straight past that guard and replaced
a complete 22-session partition with a stub. Nothing logged a loss, because
from the store's point of view a successful fetch had been written.

That is the failure mode a bulk `force_refresh` re-harvest invites, which is
why this guard went in BEFORE re-sourcing ~500 symbols off IBKR (15 Aug 2026).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from tradepro_strategies.bar_cache import asset_classes as _ac  # noqa: F401
from tradepro_strategies.bar_cache.errors import BarFetchError
from tradepro_strategies.bar_cache.store import BarStore

UTC = timezone.utc


def _bars(days: list[int], source: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex(
        [datetime(2026, 7, d, 13, 30, tzinfo=UTC) for d in days], name="timestamp")
    return pd.DataFrame({
        "open": [10.0] * len(days), "high": [11.0] * len(days),
        "low": [9.0] * len(days), "close": [10.5] * len(days),
        "volume": [1000] * len(days), "adj_factor": [1.0] * len(days),
        "source": [source] * len(days),
    }, index=idx)


@pytest.fixture
def store(tmp_path: Path) -> BarStore:
    return BarStore(base_dir=tmp_path, provider_chain=["yfinance"])


def _write(store: BarStore, df: pd.DataFrame, *, provider: str):
    from tradepro_strategies.bar_cache.asset_class import get_asset_class
    plugin = get_asset_class("us_equity")
    base = store.base_dir if hasattr(store, "base_dir") else None
    ppath = store._partition_path("TEST", "us_equity", "1d", "2026-07")
    mpath = store._manifest_path("TEST", "us_equity", "1d", "2026-07")
    store._resolved_chain_for_call = [provider]
    store._write_partition(
        df=df, plugin=plugin, canonical="TEST", asset_class="us_equity",
        resolution="1d", partition="2026-07",
        partition_start=datetime(2026, 7, 1, tzinfo=UTC),
        partition_end=datetime(2026, 8, 1, tzinfo=UTC),
        partition_path=ppath, manifest_path=mpath,
        provider_used=provider, provider_meta={}, fetched_by="test")
    return ppath


class TestShrinkGuard:
    def test_a_partial_refetch_cannot_replace_a_complete_month(self, store):
        """THE regression. 22 cached sessions, provider answers with 5."""
        full = list(range(1, 23))
        p = _write(store, _bars(full, "yfinance"), provider="yfinance")
        assert len(pd.read_parquet(p)) == 22

        with pytest.raises(BarFetchError) as ei:
            _write(store, _bars(full[:5], "ibkr_web"), provider="ibkr_web")

        assert ei.value.error_class == "partial_write_refused"
        # ...and the good data is STILL THERE, untouched.
        kept = pd.read_parquet(p)
        assert len(kept) == 22
        assert set(kept["source"]) == {"yfinance"}

    def test_the_error_names_both_counts_so_the_loss_is_quantified(self, store):
        p = _write(store, _bars(list(range(1, 23)), "yfinance"), provider="yfinance")
        with pytest.raises(BarFetchError) as ei:
            _write(store, _bars([1, 2], "ibkr_web"), provider="ibkr_web")
        msg = str(ei.value)
        assert "2" in msg and "22" in msg

    def test_equal_size_resource_is_allowed_this_is_the_whole_point(self, store):
        """Re-sourcing a month yfinance → ibkr_web keeps the session count and
        MUST go through; the guard exists to stop loss, not to freeze the store."""
        days = list(range(1, 23))
        p = _write(store, _bars(days, "yfinance"), provider="yfinance")
        _write(store, _bars(days, "ibkr_web"), provider="ibkr_web")
        after = pd.read_parquet(p)
        assert len(after) == 22
        assert set(after["source"]) == {"ibkr_web"}, "the re-source must land"

    def test_a_larger_write_is_allowed(self, store):
        p = _write(store, _bars([1, 2, 3], "yfinance"), provider="yfinance")
        _write(store, _bars([1, 2, 3, 4, 5], "ibkr_web"), provider="ibkr_web")
        assert len(pd.read_parquet(p)) == 5

    def test_empty_write_over_good_data_is_still_skipped_silently(self, store):
        """The pre-existing empty guard returns rather than raising — keep that
        behaviour so this change doesn't alter the total-failure path."""
        p = _write(store, _bars([1, 2, 3], "yfinance"), provider="yfinance")
        _write(store, _bars([], "ibkr_web"), provider="ibkr_web")
        assert len(pd.read_parquet(p)) == 3

    def test_first_write_into_an_empty_partition_is_unaffected(self, store):
        p = _write(store, _bars([1, 2, 3], "ibkr_web"), provider="ibkr_web")
        assert len(pd.read_parquet(p)) == 3
