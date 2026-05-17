"""Secret lookup with sane fallback order.

Use `get_secret(name)` everywhere code needs a credential. The lookup
order is intentional:

  1. **Env var** (e.g. `TRADEPRO_T212_API_KEY`). Fastest, zero IAM,
     keeps the dev loop tight — no AWS calls during a normal Mac
     session unless you opt in.
  2. **AWS Secrets Manager** under the prefix `/tradepro/<name>`.
     Used when `TRADEPRO_USE_AWS_SECRETS=1` is set OR when the env
     var is missing but boto3 is installed and credentials are
     available. Cached in-process so each secret is fetched once
     per worker run.
  3. **File** at `~/.tradepro/credentials` (JSON dict). Legacy
     fallback for `api_token` / `api_base_url`. New secrets go to
     SM, not here.

Secret names are kebab-case under `/tradepro/` in SM:
    /tradepro/t212-api-key
    /tradepro/t212-api-secret
    /tradepro/finnhub-api-key
    /tradepro/api-token
    /tradepro/api-base-url
    /tradepro/ibkr-account

Env-var names are the screaming-snake-case equivalent prefixed
`TRADEPRO_` so `t212-api-key` ↔ `TRADEPRO_T212_API_KEY`.

Why this exists: was — keys lived in `~/.zshrc` and
`~/.tradepro/credentials`. Per-machine secret sprawl, no rotation
story, no audit trail. AWS SM gives us all three (versioning,
rotation, CloudTrail) while keeping local dev frictionless via the
env-var fast path.

Setting up Mac-side AWS creds:
    aws configure --profile tradepro     # IAM user with
                                         #   secretsmanager:GetSecretValue
                                         # scoped to arn:aws:secretsmanager:*:*:secret:/tradepro/*
    export AWS_PROFILE=tradepro
    export TRADEPRO_USE_AWS_SECRETS=1

To populate SM from your current env (one-off bootstrap):
    aws secretsmanager create-secret \\
      --name /tradepro/t212-api-key \\
      --secret-string "$TRADEPRO_T212_API_KEY"
    # repeat for each key
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

    # AWS Secrets Manager. Opt-in via TRADEPRO_USE_AWS_SECRETS=1 OR
    # automatic if boto3 is available + creds are configured.
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
    _CACHE.clear()


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
