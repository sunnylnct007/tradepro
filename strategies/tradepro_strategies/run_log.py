"""Central run-log writer — POST operational events to /api/ingest/run-log so every
process on every machine (Mac daemons, harvest, audits) records what it did + any
failure in ONE place, surfaced loud on the cockpit
([[feedback_central_observability_fail_loud]]).

Best-effort by design: observability must NEVER break or block the operation it's
observing, so all delivery errors are swallowed HERE (the one place swallowing is
correct — a failed log post must not fail a trade/harvest). The EVENTS themselves are
fail-loud (status + error carry the reason).
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("tradepro.run_log")
_HOST = socket.gethostname()


def _iso(t: Any) -> str | None:
    if t is None:
        return None
    return t.isoformat() if hasattr(t, "isoformat") else str(t)


def log_runs(events: list[dict], *, base: str | None = None, token: str | None = None) -> bool:
    """Append a batch of run events. Each: {process, kind, status, broker?, symbol?,
    error?, summary?, started_at_utc?, finished_at_utc?}. Returns True on delivery."""
    try:
        import requests
        if base is None:
            from .cli import push_to_api
            base, token = push_to_api.load_credentials()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        for e in events:
            e.setdefault("machine", _HOST)
            e.setdefault("finished_at_utc", _iso(datetime.now(timezone.utc)))
        resp = requests.post(f"{base.rstrip('/')}/api/ingest/run-log",
                             headers=headers, json={"events": events}, timeout=10)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001 — never let logging break the op
        _log.debug("run_log delivery failed (non-fatal): %s", exc)
        return False


def log_run(process: str, kind: str, status: str, *, broker: str | None = None,
            symbol: str | None = None, error: str | None = None, summary: str | None = None,
            started: Any = None, finished: Any = None,
            base: str | None = None, token: str | None = None) -> bool:
    """Append a single run event. status ∈ ok|fail|partial|stale|warn. On fail/warn,
    pass `error` (fail-loud — the reason must never be lost)."""
    return log_runs([{
        "process": process, "kind": kind, "status": status,
        "broker": broker, "symbol": symbol, "error": error, "summary": summary,
        "started_at_utc": _iso(started), "finished_at_utc": _iso(finished),
    }], base=base, token=token)
