"""The MCP container's env carries the SES user's static keys, which S3 denies.
boto3's default chain takes env keys first, so the store must be able to sign
with the instance role ONLY — the Python twin of the API's
BarCache__CredentialSource=instance-profile (12 Nov 2026)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tradepro_strategies.bar_cache import s3_mirror


def test_instance_profile_switch_skips_the_env_keys(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_CREDENTIALS", "instance-profile")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "SES-USER-KEY")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SES-USER-SECRET")
    seen = {}
    monkeypatch.setattr(s3_mirror, "_instance_profile_client",
                        lambda region: seen.setdefault("client", f"role@{region}"))
    m = s3_mirror.S3Mirror("bucket", "bar_cache", tmp_path)
    assert m._s3() == "role@eu-west-2"


def test_no_reachable_role_fails_loud_and_the_store_degrades_to_absent(
        monkeypatch, tmp_path: Path):
    monkeypatch.setenv("TRADEPRO_BAR_CACHE_S3_CREDENTIALS", "instance-profile")

    def _no_role(region):
        raise RuntimeError("no instance role")
    monkeypatch.setattr(s3_mirror, "_instance_profile_client", _no_role)
    m = s3_mirror.S3Mirror("bucket", "bar_cache", tmp_path)
    # download FAILS LOUD (22 Nov 2026): a store we cannot reach is not an
    # empty store. Listing stays "could not ask" (None).
    from tradepro_strategies.bar_cache.errors import SharedStoreUnreachable
    with pytest.raises(SharedStoreUnreachable):
        m.download(tmp_path / "us_etf" / "SPY" / "1d" / "2026-11.parquet")
    assert not (tmp_path / "us_etf").exists(), "a failed download must not leave an empty symbol dir"
    assert m.list_names(tmp_path / "us_etf" / "SPY" / "1d") is None
