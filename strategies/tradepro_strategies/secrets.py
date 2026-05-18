"""Secret lookup with sane fallback order.

Use `get_secret(name)` everywhere code needs a credential. The lookup
order is intentional:

  1. **Env var** (e.g. `TRADEPRO_T212_API_KEY`). Fastest, zero IAM,
     keeps the dev loop tight — no AWS calls during a normal Mac
     session unless you opt in.
  2. **AWS Secrets Manager bundle** at `tradepro/all` (one secret,
     JSON key/value blob). Fetched once per worker run, cached
     in-process. Region defaults to `eu-north-1` — override with
     `TRADEPRO_AWS_REGION`.
  3. **AWS Secrets Manager per-name** under `/tradepro/<name>`.
     Legacy path, kept so older entries still resolve.
  4. **File** at `~/.tradepro/credentials` (JSON dict). Legacy
     fallback for `api_token` / `api_base_url`.

Secret keys are kebab-case (`t212-api-key`, `api-token`, …) — they
match the keys inside the bundle and the per-name SM names.

Env-var names are the screaming-snake-case equivalent prefixed
`TRADEPRO_` so `t212-api-key` ↔ `TRADEPRO_T212_API_KEY`.

Setting up Mac-side AWS creds (SSO):
    aws sso login --profile infoccit-admin
    export AWS_PROFILE=infoccit-admin
    # (no opt-in flag needed; bundle fetch is automatic when boto3
    # + creds are available)

To populate the bundle (one-time, via Console):
    Secrets Manager → Store a new secret → Other type →
    Key/value tab → add rows (t212-api-key, api-token, etc.) →
    name = `tradepro/all`, region = eu-north-1.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional


log = logging.getLogger("tradepro.secrets")

_CACHE: dict[str, Optional[str]] = {}
_CRED_PATH = Path.home() / ".tradepro" / "credentials"
_SM_PREFIX = "/tradepro/"
_SM_BUNDLE_NAME = "tradepro/all"
_SM_DEFAULT_REGION = "eu-north-1"

# Populated lazily on first successful bundle fetch. `None` = not yet
# fetched; empty dict = fetched but unavailable (don't retry).
_BUNDLE: Optional[dict[str, str]] = None
_BUNDLE_FETCHED = False


def get_secret(name: str, *, required: bool = False) -> Optional[str]:
    """Resolve a secret. `name` is the kebab-case key (e.g.
    `t212-api-key`). Lookup order: env → AWS SM → ~/.tradepro/credentials.
    Returns None if not found (or raises if `required=True`)."""
    if name in _CACHE:
        return _CACHE[name]

    # Env-var fast path.
    env_name = "TRADEPRO_" + name.upper().replace("-", "_")
    env_val = os.environ.get(env_name)
    if env_val:
        _CACHE[name] = env_val
        return env_val

    # AWS Secrets Manager bundle (one secret, JSON key/value blob).
    # Fetched once per process; subsequent lookups are dict reads.
    bundle_val = _try_bundle(name)
    if bundle_val is not None:
        _CACHE[name] = bundle_val
        return bundle_val

    # Legacy per-name SM path (`/tradepro/<name>`). Kept so older
    # entries still resolve until everything moves into the bundle.
    sm_val = _try_aws_secrets_manager(name)
    if sm_val is not None:
        _CACHE[name] = sm_val
        return sm_val

    # Legacy file fallback — only for api-token / api-base-url which
    # have lived in ~/.tradepro/credentials for a while.
    file_val = _try_credentials_file(name)
    if file_val is not None:
        _CACHE[name] = file_val
        return file_val

    if required:
        raise RuntimeError(
            f"Secret {name!r} not found in env ({env_name}), "
            f"AWS Secrets Manager ({_SM_PREFIX}{name}), or {_CRED_PATH}"
        )
    _CACHE[name] = None
    return None


def clear_cache() -> None:
    """Drop the in-process cache. Use in tests or after rotating a
    secret mid-process."""
    global _BUNDLE, _BUNDLE_FETCHED
    _CACHE.clear()
    _BUNDLE = None
    _BUNDLE_FETCHED = False


def _try_bundle(name: str) -> Optional[str]:
    """Look up `name` in the `tradepro/all` SM bundle. Fetches the
    bundle once per process and caches it; subsequent calls are dict
    reads. Returns None when the bundle is unreachable or the key
    isn't present, so the caller falls through to the next source."""
    global _BUNDLE, _BUNDLE_FETCHED
    if not _BUNDLE_FETCHED:
        _BUNDLE = _fetch_bundle()
        _BUNDLE_FETCHED = True
    if not _BUNDLE:
        return None
    return _BUNDLE.get(name)


def _fetch_bundle() -> Optional[dict[str, str]]:
    """Best-effort one-shot fetch of `tradepro/all`. Returns None on
    any failure — boto3 missing, no creds, wrong region, secret not
    found, JSON malformed."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        log.debug("boto3 not installed — bundle fetch skipped")
        return None
    region = os.environ.get("TRADEPRO_AWS_REGION", _SM_DEFAULT_REGION)
    try:
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=_SM_BUNDLE_NAME)
        body = resp.get("SecretString")
        if not body:
            return None
        data = json.loads(body)
        if not isinstance(data, dict):
            log.warning("SM bundle %s is not a JSON object", _SM_BUNDLE_NAME)
            return None
        # Coerce values to str; SM key/value tab always serializes as
        # strings but be defensive in case someone hand-edits the JSON.
        return {k: ("" if v is None else str(v)) for k, v in data.items()}
    except (BotoCoreError, ClientError) as e:
        log.debug("SM bundle fetch failed (region=%s): %s", region, e)
        return None
    except json.JSONDecodeError as e:
        log.warning("SM bundle %s is not valid JSON: %s", _SM_BUNDLE_NAME, e)
        return None
    except Exception:
        log.exception("unexpected error fetching SM bundle %s", _SM_BUNDLE_NAME)
        return None


def _try_aws_secrets_manager(name: str) -> Optional[str]:
    """Best-effort SM lookup. Silently returns None on any failure
    (boto3 missing, no creds, secret not found) — the caller falls
    through to the next source. Failures are logged at DEBUG so prod
    can be loud about them via the standard logging config."""
    opt_in = os.environ.get("TRADEPRO_USE_AWS_SECRETS") == "1"
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:
        if opt_in:
            log.warning("TRADEPRO_USE_AWS_SECRETS=1 but boto3 not installed")
        return None
    try:
        client = boto3.client("secretsmanager")
        resp = client.get_secret_value(SecretId=f"{_SM_PREFIX}{name}")
        # SM returns either SecretString (the common case) or
        # SecretBinary. We only store strings.
        return resp.get("SecretString")
    except (BotoCoreError, ClientError) as e:
        if opt_in:
            log.warning(
                "AWS Secrets Manager fetch failed for %s: %s",
                f"{_SM_PREFIX}{name}", e,
            )
        else:
            log.debug("SM lookup skipped for %s: %s", name, e)
        return None
    except Exception:
        log.exception("unexpected error fetching secret %s", name)
        return None


def _try_credentials_file(name: str) -> Optional[str]:
    """Read ~/.tradepro/credentials (JSON). Maps kebab-case secret
    name → snake_case key in the file:
        api-token   ↔  api_token
        api-base-url ↔ api_base_url"""
    if not _CRED_PATH.exists():
        return None
    try:
        data = json.loads(_CRED_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Could not read %s: %s", _CRED_PATH, e)
        return None
    if not isinstance(data, dict):
        return None
    snake = name.replace("-", "_")
    return data.get(snake)


__all__ = ["get_secret", "clear_cache"]
