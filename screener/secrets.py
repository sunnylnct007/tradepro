"""AWS Secrets Manager loader for Lambda credentials."""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("screener.secrets")

_SECRET_NAME = os.environ.get("TRADEPRO_SECRET_NAME", "tradepro/screener")
_REGION = os.environ.get("AWS_REGION", "eu-west-2")

_cache: dict = {}


def get_secret(key: str) -> str:
    """Retrieve a key from the cached Secrets Manager secret."""
    if not _cache:
        _load()
    val = _cache.get(key) or os.environ.get(key, "")
    if not val:
        raise RuntimeError(f"Secret '{key}' not found in '{_SECRET_NAME}' or environment")
    return val


def _load() -> None:
    try:
        import boto3
        client = boto3.client("secretsmanager", region_name=_REGION)
        resp = client.get_secret_value(SecretId=_SECRET_NAME)
        data = json.loads(resp["SecretString"])
        _cache.update(data)
        log.info("Loaded %d keys from Secrets Manager (%s)", len(data), _SECRET_NAME)
    except Exception as e:  # noqa: BLE001
        log.warning("Secrets Manager unavailable (%s) — falling back to env vars", e)
