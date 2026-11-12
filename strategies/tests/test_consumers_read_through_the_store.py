"""Every consumer reads bars through the store, and the store reads S3 when
this machine holds nothing (DATA_ARCHITECTURE step 5, 12 Nov 2026).

WHY. Six private loaders globbed ~/.tradepro/bar_cache. On the harvesting Mac
that works. On Lambda the directory is empty, so the today-setups job scanned
0 of 113 names on 11 Nov and published all of them as "missing", and nothing
failed. The put check went further: it built `BarStore()` without its required
directory, the call raised, and an except turned that into "no data" for every
symbol.

These tests stand in a second, EMPTY store for the Lambda host, give it the
harvester's partitions only through an S3 stand-in, and require that it read
back exactly what the harvester holds, without ever calling a provider.
"""
from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from tradepro_strategies.bar_cache import BarStore
from tradepro_strategies.bar_cache.asset_classes import UsEtfPlugin
from tradepro_strategies.bar_cache.providers import Provider, register_provider

_PLUGIN = UsEtfPlugin()


def _bars(start: datetime, end: datetime) -> pd.DataFrame:
    days = [d for d in _PLUGIN.expected_session_dates(start, end)
            if datetime(d.year, d.month, d.day, tzinfo=UTC) < end]
    idx = pd.DatetimeIndex([datetime(d.year, d.month, d.day, 13, 30, tzinfo=UTC)
                            for d in days])
    n = len(idx)
    closes = [100.0 + i for i in range(n)]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": closes, "volume": [1000] * n,
                         "adj_factor": [1.0] * n, "source": ["test"] * n},
                        index=idx)


class _Provider(Provider):
    def __init__(self, name: str, *, forbidden: bool = False):
        self.name = name
        self.forbidden = forbidden
        self.calls = 0

    def fetch(self, canonical, asset_class, resolution, start, end):
        self.calls += 1
        if self.forbidden:
            raise AssertionError("a READ must never call a provider")
        df = _bars(start, end)
        return df, {"provider_version": "test", "rows": len(df)}

    def max_history(self, resolution):
        return None


class _FakeS3:
    """The shared bucket, played by the harvester's directory."""

    def __init__(self, remote_root: Path, local_root: Path):
        self.remote, self.local = remote_root, local_root
        self.downloads = 0

    def _remote(self, path: Path) -> Path:
        return self.remote / Path(path).relative_to(self.local)

    def download(self, path: Path) -> bool:
        src = self._remote(path)
        if not src.exists():
            return False
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, path)
        self.downloads += 1
        return True

    def upload(self, path: Path) -> bool:
        return False

    def list_names(self, directory: Path):
        d = self._remote(directory)
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []


@pytest.fixture()
def harvester_and_lambda(tmp_path, monkeypatch, request):
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_DISABLE", "1")
    tag = request.node.name[:40]
    writer = _Provider(f"writer_{tag}")
    reader = _Provider(f"reader_{tag}", forbidden=True)
    register_provider(writer)
    register_provider(reader)

    harvester = BarStore(base_dir=tmp_path / "mac", provider_chain=[writer.name])
    harvester.get("SPY", "us_etf", "1d", datetime(2025, 6, 1, tzinfo=UTC),
                  datetime(2025, 9, 1, tzinfo=UTC), allow_partial=True)

    lam = BarStore(base_dir=tmp_path / "lambda", provider_chain=[reader.name])
    lam._s3 = _FakeS3(tmp_path / "mac", tmp_path / "lambda")
    return harvester, lam, reader


def test_an_empty_host_reads_the_harvesters_history_from_s3(harvester_and_lambda):
    harvester, lam, reader = harvester_and_lambda
    # Listed from S3 alone — the Lambda host holds no file yet.
    assert lam.stored_partitions("SPY", "us_etf", "1d")[:3] == ["2025-06", "2025-07", "2025-08"]

    want = harvester.read("SPY", "us_etf", "1d").df
    got = lam.read("SPY", "us_etf", "1d").df

    assert len(want) > 60
    pd.testing.assert_frame_equal(got, want)
    assert reader.calls == 0
    assert lam._s3.downloads > 0


def test_a_symbol_the_store_does_not_hold_is_empty_not_an_error(harvester_and_lambda):
    _, lam, reader = harvester_and_lambda
    frame = lam.read("NOPE", "us_etf", "1d")
    assert frame.df.empty
    assert reader.calls == 0


def test_the_shared_loader_goes_through_the_store(harvester_and_lambda, monkeypatch):
    _, lam, _ = harvester_and_lambda
    from tradepro_strategies import daily_bars
    monkeypatch.setattr(daily_bars, "_store", lambda: lam)

    df = daily_bars.load_daily("SPY")
    assert df is not None and len(df) > 60
    assert daily_bars.load_daily("SPY", min_bars=10_000) is None
    assert daily_bars.load_daily("NOPE") is None


def _freeze_loader_clock(monkeypatch, daily_bars):
    real = daily_bars.datetime

    class _Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return real(2025, 8, 29, tzinfo=UTC)
    monkeypatch.setattr(daily_bars, "datetime", _Frozen)


def test_a_lookback_reads_only_recent_months(harvester_and_lambda, monkeypatch):
    _, lam, _ = harvester_and_lambda
    from tradepro_strategies import daily_bars
    monkeypatch.setattr(daily_bars, "_store", lambda: lam)
    _freeze_loader_clock(monkeypatch, daily_bars)
    df = daily_bars.load_bars("SPY", "1d", lookback_days=20)
    assert df is not None
    assert df.index.min() >= datetime(2025, 8, 9, tzinfo=UTC)


def test_the_put_check_no_longer_answers_no_data_for_everything(
        harvester_and_lambda, monkeypatch):
    _, lam, _ = harvester_and_lambda
    from tradepro_strategies import daily_bars
    from tradepro_strategies.cli import put_check
    monkeypatch.setattr(daily_bars, "_store", lambda: lam)
    _freeze_loader_clock(monkeypatch, daily_bars)
    assert put_check._load("SPY") is not None


def test_a_futures_symbol_reads_back(tmp_path, monkeypatch):
    """`CL=F` is a directory name pyarrow's dataset layer reads as a hive
    partition (`CL` = `F`). Every read of a futures series used to fail."""
    monkeypatch.delenv("TRADEPRO_BAR_CACHE_S3_BUCKET", raising=False)
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_DISABLE", "1")
    writer = _Provider("writer_futures")
    register_provider(writer)
    store = BarStore(base_dir=tmp_path, provider_chain=[writer.name])
    store.get("CL=F", "us_etf", "1d", datetime(2025, 7, 1, tzinfo=UTC),
              datetime(2025, 8, 1, tzinfo=UTC), allow_partial=True)
    df = store.read("CL=F", "us_etf", "1d").df
    assert len(df) > 15
    assert "CL" not in df.columns
