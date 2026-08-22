"""S3 mirror for the bar cache — the SHARED source of truth across envs.

Design intent (small hedge firm, minimal staff):
  - Market data is environment-INDEPENDENT (IBKR's AAPL daily bar is the same
    for dev + prod), so we harvest ONCE and SHARE — no per-env harvest, no extra
    ops. S3 is the source of truth; the local dir is a fast read-through cache.
  - The harvest is the single WRITER (write-through to S3 after each atomic local
    write). Every env READS from S3 on a local miss (read-through), then caches
    locally so backtests don't re-pull.
  - FAIL-SAFE above all: if S3 is unreachable / creds absent / object missing,
    we log and fall back to LOCAL — never crash the store. A data-store hiccup
    must not halt trading or backtests when there's no one to babysit it.

Enable by setting TRADEPRO_BAR_CACHE_S3_BUCKET (+ optional _S3_PREFIX, default
"bar_cache"). Unset → pure local (dev), zero behaviour change. Boto3 resolves
creds the standard way (env / SSO profile / instance role) so the SAME code runs
on a laptop (SSO) and in prod (instance role) with no edits.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

_log = logging.getLogger("tradepro.bar_cache.s3")



def _scoped_conf(key: str) -> Optional[str]:
    """Read one value from ~/.tradepro/credentials, or None."""
    try:
        import json
        return json.loads(
            (Path.home() / ".tradepro" / "credentials").read_text()).get(key)
    except Exception:  # noqa: BLE001 — absent creds is a normal dev state
        return None


def _scoped_mirror_keys() -> tuple[Optional[str], Optional[str]]:
    """The bar-mirror IAM keys from ~/.tradepro/credentials, or (None, None).
    Read-and-write, deliberately WITHOUT s3:DeleteObject."""
    try:
        import json
        creds = json.loads(
            (Path.home() / ".tradepro" / "credentials").read_text())
        return (creds.get("bar-mirror-aws-access-key-id") or None,
                creds.get("bar-mirror-aws-secret-access-key") or None)
    except Exception:  # noqa: BLE001 — absent creds is a normal dev state
        return (None, None)


class S3Mirror:
    """Maps a local cache path ↔ an S3 key (same relative layout) and does
    best-effort upload/download. Every method is fail-safe: errors log + return
    a falsey result, never raise into the BarStore."""

    def __init__(self, bucket: str, prefix: str, base_dir: Path) -> None:
        self.bucket = bucket
        self.prefix = (prefix or "").strip("/")
        self.base_dir = Path(base_dir).resolve()
        self._client = None  # lazy — don't pay boto3 import/creds until used

    def _s3(self):
        if self._client is None:
            import boto3  # lazy import: only when S3 is actually configured
            # Credential resolution, in order:
            #   1. boto3's standard chain — env vars, instance role (EC2),
            #      SSO profile. This is what prod uses; nothing to configure.
            #   2. The scoped bar-mirror keys in ~/.tradepro/credentials.
            # (2) exists because the Mac lanes run under launchd with no SSO
            # session — `aws sso login` expires and every daemon would silently
            # lose S3 (22 Aug 2026). The nightly mirror script already reads
            # these keys; the store now shares them so read-through works from
            # a daemon without anyone re-authenticating each morning. The key
            # is deliberately delete-less, so this cannot wipe the backup.
            region = (os.environ.get("AWS_REGION")
                      or os.environ.get("TRADEPRO_AWS_REGION") or "eu-west-2")
            try:
                client = boto3.client("s3", region_name=region)
                # Force credential resolution NOW so a missing chain falls
                # through to (2) rather than failing on the first real call.
                if client._request_signer._credentials is None:  # noqa: SLF001
                    raise RuntimeError("no credentials in the default chain")
                self._client = client
            except Exception:  # noqa: BLE001 — fall through to scoped keys
                ak, sk = _scoped_mirror_keys()
                if not (ak and sk):
                    raise
                self._client = boto3.client(
                    "s3", region_name=region,
                    aws_access_key_id=ak, aws_secret_access_key=sk)
                _log.info("bar cache S3: using scoped bar-mirror credentials")
        return self._client

    def _key(self, path: Path) -> Optional[str]:
        try:
            rel = Path(path).resolve().relative_to(self.base_dir)
        except ValueError:
            return None  # path outside the cache root — never mirror it
        return f"{self.prefix}/{rel}" if self.prefix else str(rel)

    def upload(self, path: Path) -> bool:
        """Write-through: push a finalized local file to S3. Best-effort."""
        key = self._key(path)
        if key is None or not Path(path).exists():
            return False
        try:
            self._s3().upload_file(str(path), self.bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001 — fail-safe, never crash a write
            _log.warning("S3 upload failed for %s (kept local): %s", key, exc)
            return False

    def download(self, path: Path) -> bool:
        """Read-through: fetch an object into the local cache on a local miss.
        Returns True if the file is now local (downloaded). False = not in S3
        (genuine miss) OR S3 unavailable — caller then treats it as absent."""
        key = self._key(path)
        if key is None:
            return False
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._s3().download_file(self.bucket, key, str(path))
            return True
        except Exception as exc:  # noqa: BLE001
            # 404 = genuine miss (not yet harvested); anything else = S3 issue →
            # still fall back to "absent" so the store keeps working locally.
            code = getattr(getattr(exc, "response", None), "get", lambda *_: None)("Error")
            if not (isinstance(code, dict) and code.get("Code") in ("404", "NoSuchKey")):
                _log.warning("S3 download failed for %s (local fallback): %s", key, exc)
            return False


def mirror_from_env(base_dir: Path) -> Optional[S3Mirror]:
    """Build an S3Mirror from env, or None (pure-local) when unconfigured.
    TRADEPRO_BAR_CACHE_S3_BUCKET is the single switch — set it and the SAME
    code shares the cache; unset and it's local-only (dev)."""
    # Explicit off-switch. Needed because the credentials fallback below
    # would otherwise enable S3 inside unit tests on any machine that has
    # the file — turning offline unit tests into network tests.
    if (os.environ.get("TRADEPRO_BAR_CACHE_S3_DISABLE") or "").strip().lower() \
            in ("1", "true", "yes", "on"):
        return None
    bucket = (os.environ.get("TRADEPRO_BAR_CACHE_S3_BUCKET") or "").strip()
    if not bucket:
        # Fall back to ~/.tradepro/credentials (key: bar-cache-s3-bucket).
        # 22 Aug 2026 — owner ruling "we should read from S3". Configuring it
        # here rather than in 15 launchd plists means every lane picks it up
        # with no per-plist edit and no secret in a plist file; a machine
        # without the credentials file stays purely local, unchanged.
        bucket = (_scoped_conf("bar-cache-s3-bucket") or "").strip()
    if not bucket:
        return None
    prefix = os.environ.get("TRADEPRO_BAR_CACHE_S3_PREFIX", "bar_cache")
    _log.info("bar cache: S3 shared store enabled (s3://%s/%s)", bucket, prefix.strip("/"))
    return S3Mirror(bucket, prefix, base_dir)
