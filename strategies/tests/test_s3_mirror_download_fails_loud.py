"""22 Nov 2026: a Fargate task with the bar cache on EFS and /tmp on the
container saw EVERY S3 download fail (rename across filesystems), read the
golden store as empty, re-harvested 10 days and wrote them through to S3.
Pins: the temp file is created beside its destination; a 404 is a miss; any
other failure raises instead of reading as "absent"."""
from __future__ import annotations

from pathlib import Path

import pytest

from tradepro_strategies.bar_cache.errors import SharedStoreUnreachable
from tradepro_strategies.bar_cache.s3_mirror import S3Mirror


class _ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Fake:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.seen = []

    def download_file(self, bucket, key, dst):
        self.seen.append(Path(dst))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        Path(dst).write_bytes(b"parquet")


def _mirror(tmp_path, client):
    m = S3Mirror("bucket", "bar_cache", tmp_path)
    m._client = client
    return m


def test_the_temp_file_is_created_in_the_destination_directory(tmp_path):
    client = _Fake(None)
    target = tmp_path / "us_etf" / "MU" / "1d" / "2026-11.parquet"
    assert _mirror(tmp_path, client).download(target) is True
    assert target.read_bytes() == b"parquet"
    assert client.seen[0].parent == target.parent, "a rename across filesystems fails (EXDEV)"
    assert not list(target.parent.glob(".*.part")), "no temp file left behind"


def test_a_missing_object_is_a_miss(tmp_path):
    target = tmp_path / "us_etf" / "MU" / "1d" / "1999-01.parquet"
    assert _mirror(tmp_path, _Fake(_ClientError("404"))).download(target) is False
    assert _mirror(tmp_path, _Fake(_ClientError("NoSuchKey"))).download(target) is False
    assert not target.exists()
    assert not (tmp_path / "us_etf").exists(), "22 Aug 2026: an empty dir IS a symbol to the universe"


@pytest.mark.parametrize("exc", [_ClientError("403"), OSError(18, "Invalid cross-device link"), RuntimeError("no creds")])
def test_any_other_failure_refuses_to_read_the_store_as_empty(tmp_path, exc):
    target = tmp_path / "us_etf" / "MU" / "1d" / "2026-11.parquet"
    with pytest.raises(SharedStoreUnreachable):
        _mirror(tmp_path, _Fake(exc)).download(target)
    assert not target.exists()
    assert not (tmp_path / "us_etf").exists()
