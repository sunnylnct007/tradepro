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
VALID_KINDS = {"backtest", "scan", "model_prediction", "compare", "heartbeat", "document"}


def load_credentials() -> tuple[str, str]:
    if not CRED_PATH.exists():
        print(f"credentials file not found: {CRED_PATH}", file=sys.stderr)
        sys.exit(2)
    data = json.loads(CRED_PATH.read_text())
    base = data.get("api_base_url") or os.environ.get("TRADEPRO_API_URL")
    token = data.get("api_token") or os.environ.get("TRADEPRO_API_TOKEN")
    if not base or not token:
        print("credentials must include api_base_url and api_token", file=sys.stderr)
        sys.exit(2)
    return base.rstrip("/"), token


def push(kind: str, payload: dict, base_url: str, token: str, retries: int = 4) -> None:
    url = f"{base_url}/api/ingest/{kind}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    safe_payload = scrub_for_json(payload)
    for attempt in range(retries + 1):
        try:
            resp = requests.post(url, headers=headers, json=safe_payload, timeout=30)
            if 200 <= resp.status_code < 300:
                print(f"ok: {resp.status_code}")
                return
            print(f"attempt {attempt + 1}: HTTP {resp.status_code} {resp.text[:200]}")
        except requests.RequestException as e:
            print(f"attempt {attempt + 1}: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    print("all attempts failed", file=sys.stderr)
    sys.exit(1)


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
