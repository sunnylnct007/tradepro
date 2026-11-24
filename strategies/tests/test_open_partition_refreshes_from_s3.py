"""24 Nov 2026: a warm mirror kept the open month's partition as fetched at
07:07 — no 5-minute bars for the day, and the evening tick would have judged
stops on yesterday's close. An OPEN partition is re-fetched when S3 holds a
newer object; a closed one is never re-read."""
from __future__ import annotations

import datetime as dt
import os
import time
from pathlib import Path

from tradepro_strategies.bar_cache import store as S
from tradepro_strategies.bar_cache.s3_mirror import S3Mirror


def test_open_partition_is_the_current_month_only():
    now = dt.datetime.now(dt.timezone.utc)
    cur = f"{now.year:04d}-{now.month:02d}"
    assert S._is_open_partition(Path(f"/x/us_etf/MU/1d/{cur}.parquet"))
    assert S._is_open_partition(Path(f"/x/us_etf/MU/5m/{cur}.manifest.json"))
    assert not S._is_open_partition(Path("/x/us_etf/MU/1d/2021-03.parquet"))
    assert not S._is_open_partition(Path("/x/us_etf/MU/1d/universe.json"))


class _Head:
    def __init__(self, when, raise_code=None):
        self.when, self.raise_code, self.downloads = when, raise_code, []

    def head_object(self, Bucket, Key):
        if self.raise_code:
            e = Exception(self.raise_code); e.response = {"Error": {"Code": self.raise_code}}; raise e
        return {"LastModified": self.when}

    def download_file(self, bucket, key, dst):
        self.downloads.append(key); Path(dst).write_bytes(b"fresh")


def _mirror(tmp_path, client):
    m = S3Mirror("bucket", "bar_cache", tmp_path); m._client = client; return m


def test_newer_object_in_s3_replaces_the_local_copy(tmp_path):
    f = tmp_path / "us_etf" / "MU" / "5m" / "2026-11.parquet"
    f.parent.mkdir(parents=True); f.write_bytes(b"stale")
    old = time.time() - 3600; os.utime(f, (old, old))
    client = _Head(dt.datetime.now(dt.timezone.utc))
    assert _mirror(tmp_path, client).refresh_if_newer(f) is True
    assert f.read_bytes() == b"fresh" and client.downloads


def test_older_or_equal_object_leaves_the_local_copy(tmp_path):
    f = tmp_path / "us_etf" / "MU" / "5m" / "2026-11.parquet"
    f.parent.mkdir(parents=True); f.write_bytes(b"local")
    client = _Head(dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2))
    assert _mirror(tmp_path, client).refresh_if_newer(f) is False
    assert f.read_bytes() == b"local" and not client.downloads


def test_a_failed_head_keeps_the_local_copy(tmp_path):
    f = tmp_path / "us_etf" / "MU" / "5m" / "2026-11.parquet"
    f.parent.mkdir(parents=True); f.write_bytes(b"local")
    assert _mirror(tmp_path, _Head(None, raise_code="403")).refresh_if_newer(f) is False
    assert f.read_bytes() == b"local"
