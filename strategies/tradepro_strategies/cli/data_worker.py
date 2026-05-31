"""tradepro-data-worker — polling loop for trustworthy-data ops.

Thin transport / orchestration layer. All business logic lives under
``tradepro_strategies.data_ops`` (handlers + registry + storage
abstraction). This CLI:

  1. Polls ``/api/ops/poll-data`` for claimed sessions.
  2. Wraps the response in a ``DataOpRequest``.
  3. Calls ``data_ops.dispatch(request, storage)`` to run the handler.
  4. Posts the ``DataOpResult.to_wire_dict()`` back via
     ``/api/ops/complete-data/{request_id}``.

Why this split is worth it (post-2026-05-31 operator brief on
multi-service production deployments):

  * The same data_ops package is callable from MCP tools, backend
    tests, a different worker deployment, or a Lambda — none of
    them need the poll/HTTP wrapper.
  * The storage backend is injected at CLI startup, not baked into
    the handler. Today: ``LocalBarCacheStorage(base_dir=~/.tradepro/bar_cache)``.
    Phase I will swap in ``S3BarCacheStorage`` without touching any
    handler.
  * Adding the next op kind (data_backfill, data_reload, ...) is a
    single new file under ``data_ops/handlers/``. The CLI doesn't
    change.

Multi-instance friendly: ``--instance-id`` is sent to the backend
``Claim`` so multiple workers can run concurrently without
double-claiming. The atomic UPDATE-RETURNING in session_requests
ensures only one worker wins per row.

Pause / resume without unloading launchd:
  touch ~/.tradepro/data-worker.pause
  rm    ~/.tradepro/data-worker.pause
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    print("requests required (uv pip install requests)", file=sys.stderr)
    sys.exit(2)


_log = logging.getLogger("tradepro.data_worker")

_PAUSE_FILE = Path.home() / ".tradepro" / "data-worker.pause"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Polling loop for the trustworthy-data ops queue. "
            "Dispatches claimed sessions to data_ops handlers."
        ),
    )
    parser.add_argument(
        "--api-base", required=True,
        help='API base URL (e.g. "http://localhost:5252")',
    )
    parser.add_argument(
        "--auth-token", default=None,
        help="Bearer token. Falls back to TRADEPRO_API_TOKEN env.",
    )
    parser.add_argument(
        "--poll-interval-seconds", type=float, default=10.0,
        help="Seconds between empty-queue polls. Default 10.",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process one claim then exit. Useful for cron / one-shot.",
    )
    parser.add_argument(
        "--kinds", default=None,
        help=(
            "Comma-separated op kinds to claim. Defaults to every "
            "registered handler (data_ops.list_kinds())."
        ),
    )
    parser.add_argument(
        "--instance-id", default=None,
        help=(
            "Identifier reported to the backend as `host` on claim. "
            "Defaults to the machine hostname; set this when running "
            "multiple workers on the same host."
        ),
    )
    parser.add_argument(
        "--cache-base-dir", default=None,
        help=(
            "Local bar-cache base directory. Defaults to "
            "~/.tradepro/bar_cache. In a multi-service deployment "
            "where this worker reads from a shared mount, point it "
            "at the mount path. Phase I will add --storage=s3."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Late import so the CLI imports cheaply if --help is the only goal.
    from tradepro_strategies.data_ops import (
        DataOpRequest,
        LocalBarCacheStorage,
        dispatch,
        list_kinds,
    )

    api_base = args.api_base.rstrip("/")
    token = args.auth_token or os.environ.get("TRADEPRO_API_TOKEN")
    headers = {"content-type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    host = args.instance_id or socket.gethostname()
    if args.kinds:
        kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    else:
        kinds = list_kinds()
        if not kinds:
            _log.error(
                "no data_ops handlers registered; nothing to poll for"
            )
            return 2

    base_dir = Path(
        args.cache_base_dir
        or os.environ.get("TRADEPRO_BAR_CACHE_BASE_DIR")
        or (Path.home() / ".tradepro" / "bar_cache")
    )
    base_dir.mkdir(parents=True, exist_ok=True)
    storage = LocalBarCacheStorage(base_dir=base_dir)

    _log.info(
        "data-worker starting: api_base=%s host=%s kinds=%s storage=%s once=%s",
        api_base, host, kinds, storage.describe(), args.once,
    )

    while True:
        if _PAUSE_FILE.exists():
            _log.info("paused via %s; sleeping %.1fs",
                      _PAUSE_FILE, args.poll_interval_seconds)
            time.sleep(args.poll_interval_seconds)
            continue

        session = _poll_one(api_base, headers, kinds, host)
        if session is None:
            if args.once:
                _log.info("nothing to claim; exiting --once")
                return 0
            time.sleep(args.poll_interval_seconds)
            continue

        request_id = session.get("request_id") or session.get("requestId")
        kind = session.get("kind") or ""
        params = session.get("params") or {}
        _log.info("claimed %s (%s) params=%s", request_id, kind, params)

        request = DataOpRequest(
            request_id=str(request_id),
            kind=str(kind),
            params=dict(params) if isinstance(params, dict) else {},
        )

        try:
            result = dispatch(request, storage)
        except Exception as exc:  # noqa: BLE001
            _log.exception("dispatch raised for %s", request_id)
            _post_complete(
                api_base, headers, str(request_id),
                status="failed",
                error=f"{type(exc).__name__}: {exc}",
            )
            if args.once:
                return 1
            continue

        if result.ok:
            _post_complete(
                api_base, headers, str(request_id),
                status="completed",
                result_summary=result.to_wire_dict(),
            )
            _log.info("completed %s: %s", request_id, result.summary)
        else:
            # Handler-reported (operator-correctable) failure. Status
            # is 'completed' on the queue (handler ran fine) but the
            # detail records ok=False so the cockpit displays it as
            # a soft failure that the operator can act on.
            _post_complete(
                api_base, headers, str(request_id),
                status="completed",
                result_summary=result.to_wire_dict(),
            )
            _log.info("completed-with-handler-error %s: %s",
                      request_id, result.summary)

        if args.once:
            return 0


def _poll_one(
    api_base: str,
    headers: dict[str, str],
    kinds: list[str],
    host: str,
) -> Optional[dict[str, Any]]:
    try:
        resp = requests.post(
            f"{api_base}/api/ops/poll-data",
            json={"kinds": kinds, "host": host},
            headers=headers,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("poll failed (%s)", exc)
        return None
    if not resp.ok:
        _log.warning(
            "poll returned %s: %s",
            resp.status_code, resp.text[:200],
        )
        return None
    body = resp.json() or {}
    if not body.get("claimed"):
        return None
    return body.get("session") or {}


def _post_complete(
    api_base: str,
    headers: dict[str, str],
    request_id: str,
    *,
    status: str,
    result_summary: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    body: dict[str, Any] = {"status": status}
    if result_summary is not None:
        body["result_summary"] = result_summary
    if error is not None:
        body["error"] = error
    try:
        resp = requests.post(
            f"{api_base}/api/ops/complete-data/{request_id}",
            json=body, headers=headers, timeout=30,
        )
        if not resp.ok:
            _log.warning(
                "complete-data returned %s: %s",
                resp.status_code, resp.text[:200],
            )
    except Exception as exc:  # noqa: BLE001
        _log.warning("complete-data failed: %s", exc)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
