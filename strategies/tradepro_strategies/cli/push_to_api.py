"""Push a local result JSON to the TradePro API.

The website never connects to your Mac. Your Mac pushes results up instead:

    uv run tradepro-push --kind backtest ../out/barc_sma.json

Credentials: put in `~/.tradepro/credentials` as JSON, e.g.
    { "api_base_url": "https://tradepro-api-g2ardxhffph4fbdr.canadacentral-01.azurewebsites.net",
      "api_token":    "<long-random-string>" }

Matching env on Azure App Service: set `Ingest__Token` to the same value.
The API endpoints (`POST /api/ingest/<kind>`) are rolled out in Phase 1 of the
roadmap — this script is the client end of that contract.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import requests


def scrub_for_json(obj):
    """Recursively replace NaN / +-Inf floats with None so a payload
    serialises to RFC 8259 JSON. The comparator emits NaN as a
    'missing stat' sentinel via _safe_float (in compare.py); without
    this scrub, requests' JSON serializer raises 'Out of range float
    values are not JSON compliant: nan' and the push retries to
    exhaustion. The .NET API rejects NaN literals too, so emitting
    null is the only correct wire shape."""
    if isinstance(obj, dict):
        return {k: scrub_for_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_for_json(v) for v in obj]
    if isinstance(obj, tuple):
        return [scrub_for_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj

CRED_PATH = Path.home() / ".tradepro" / "credentials"
VALID_KINDS = {"backtest", "scan", "model_prediction", "compare", "heartbeat", "document", "paper-backtest"}


def load_credentials() -> tuple[str, str]:
    """Resolve (api_base_url, api_token). File is preferred when present
    (matches the macOS / launchd path); env vars fill in when the file
    is absent (matches the docker-compose worker path). Without either,
    we exit non-zero so the caller can surface a clean error."""
    data: dict = {}
    if CRED_PATH.exists():
        try:
            data = json.loads(CRED_PATH.read_text())
        except (OSError, json.JSONDecodeError) as e:
            print(f"warning: could not read {CRED_PATH}: {e}", file=sys.stderr)
    base = data.get("api_base_url") or os.environ.get("TRADEPRO_API_URL")
    token = data.get("api_token") or os.environ.get("TRADEPRO_API_TOKEN")
    if not base or not token:
        print(
            "credentials must include api_base_url and api_token — "
            f"checked {CRED_PATH} and TRADEPRO_API_URL / TRADEPRO_API_TOKEN env",
            file=sys.stderr,
        )
        sys.exit(2)
    return base.rstrip("/"), token


def push(kind: str, payload: dict, base_url: str, token: str, retries: int = 6) -> None:
    """POST a payload with exponential backoff. Retry count tuned to
    survive a Caddy cert reload on the AWS box (which can drop TLS
    mid-handshake for ~30-60s during /api/redeploy). Backoff schedule
    with retries=6: 1, 2, 4, 8, 16, 32s → ~63s total — wider than the
    worst-case TLS hiccup we've seen in the logs.

    SSL EOF errors get logged distinctly so the operator can tell
    "AWS box was redeploying" apart from "endpoint is genuinely down".
    """
    url = f"{base_url}/api/ingest/{kind}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    safe_payload = scrub_for_json(payload)
    last_error: str | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=safe_payload, timeout=30)
            if 200 <= resp.status_code < 300:
                print(f"ok: {resp.status_code}")
                _maybe_archive_to_s3(kind, safe_payload)
                return
            last_error = f"HTTP {resp.status_code} {resp.text[:200]}"
            # 5xx is retryable; 4xx (other than 429) is not — abort early.
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                print(f"attempt {attempt + 1}: {last_error} (not retrying — client error)")
                break
            print(f"attempt {attempt + 1}: {last_error}")
        except requests.exceptions.SSLError as e:
            # nginx terminating the connection with a 413
            # (request-entity-too-large) surfaces as an SSL EOF
            # because the TLS pipe gets closed mid-upload. Caddy
            # cert reloads look the same on the wire. Either way:
            # retryable, log clearly so operator can grep.
            last_error = f"SSL EOF (nginx 413 or Caddy reload): {e}"
            print(f"attempt {attempt + 1}: {last_error}")
        except requests.RequestException as e:
            last_error = str(e)
            print(f"attempt {attempt + 1}: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    print(f"all attempts failed — last: {last_error}", file=sys.stderr)
    sys.exit(1)


def _maybe_archive_to_s3(kind: str, payload: dict) -> None:
    """Optional: push a copy of the payload to S3 for replay history.

    Opt-in at TWO layers so the default behaviour is unchanged:
      1. `TRADEPRO_S3_ARCHIVE=1` env var must be set
      2. boto3 must be importable (not in the default deps — install
         via `pip install boto3` in the worker's venv)

    Either layer absent → silent skip, never fails the upstream push.
    Same applies for any S3 / network error: the archive is a "nice
    to have", not load-bearing for the API ingest contract.

    Object key: `<kind>/<universe-or-host>/<run-id-or-timestamp>.json`
    so the bucket layout reads naturally with `aws s3 ls`.

    Bucket name comes from `TRADEPRO_S3_ARCHIVE_BUCKET` (defaults to
    the terraform output `ccit-dev-tradepro-archive`). Credentials
    follow boto3's normal chain — env vars / ~/.aws/credentials / IAM
    role — but we surface the dedicated writer-creds env names so the
    operator can scope them tightly without touching the global
    profile."""
    if os.environ.get("TRADEPRO_S3_ARCHIVE") != "1":
        return
    try:
        import boto3  # type: ignore[import-untyped]
    except ImportError:
        print(
            "TRADEPRO_S3_ARCHIVE=1 set but boto3 not installed — "
            "skipping archive (pip install boto3 in the worker venv "
            "to enable).",
            file=sys.stderr,
        )
        return
    bucket = os.environ.get("TRADEPRO_S3_ARCHIVE_BUCKET", "ccit-dev-tradepro-archive")
    region = os.environ.get("TRADEPRO_S3_ARCHIVE_REGION", "eu-west-2")
    # Allow dedicated writer creds (separate from any global AWS profile
    # the user has). Falls through to the standard boto3 chain when not set.
    ak = os.environ.get("TRADEPRO_S3_AWS_ACCESS_KEY_ID")
    sk = os.environ.get("TRADEPRO_S3_AWS_SECRET_ACCESS_KEY")
    kwargs: dict = {"region_name": region}
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
    key = _archive_object_key(kind, payload)
    try:
        client = boto3.client("s3", **kwargs)
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(payload, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        print(f"archived → s3://{bucket}/{key}")
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"S3 archive failed (non-fatal): {e}", file=sys.stderr)


def _archive_object_key(kind: str, payload: dict) -> str:
    """Build a stable S3 key. Prefers fields that uniquely identify the
    run (universe + run_id for compare; host + receivedAt for heartbeat)
    so re-pushing the same run overwrites cleanly rather than littering
    versions. Falls back to a UTC timestamp when those fields are missing."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if kind == "compare":
        universe = (payload.get("universe") or "unknown").replace("/", "_")
        run_id = payload.get("run_id") or ts
        return f"compare/{universe}/{run_id}.json"
    if kind == "heartbeat":
        host = (payload.get("host") or "unknown-host").replace("/", "_")
        return f"heartbeat/{host}/{ts}.json"
    return f"{kind}/{ts}.json"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--kind", required=True, choices=sorted(VALID_KINDS))
    p.add_argument("file", type=Path, help="path to a JSON payload")
    args = p.parse_args()

    payload = json.loads(args.file.read_text())
    base, token = load_credentials()
    push(args.kind, payload, base, token)


if __name__ == "__main__":
    main()
