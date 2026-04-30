"""Lightweight 'what is the Mac doing right now' state file.

The comparator and other long-running CLIs write a small JSON snapshot
to ~/.tradepro/state/current.json while they're running, and clear it
when they finish. The heartbeat module reads it; the API surfaces it
to the UI so a user can see 'Mac is currently running etf_us_core
(33% of the way through, started 12s ago)' in real time, instead of
just 'last seen 2 min ago'.

The file is atomic-rename written so a partial read can never produce
malformed JSON. If the file is missing, no task is running.
"""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_DIR = Path.home() / ".tradepro" / "state"
STATE_FILE = STATE_DIR / "current.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content)
    os.replace(tmp, path)


def write(task: str, detail: str | None = None, phase: str | None = None,
          progress: float | None = None, run_id: str | None = None) -> None:
    """Snapshot the current task. Call repeatedly with phase updates."""
    payload: dict[str, Any] = {
        "host": socket.gethostname(),
        "task": task,
        "detail": detail,
        "phase": phase,
        "progress": progress,
        "run_id": run_id,
        "started_at": _started_at_or_now(),
        "updated_at": _utc_now_iso(),
        "pid": os.getpid(),
    }
    _atomic_write(STATE_FILE, json.dumps(payload))


def _started_at_or_now() -> str:
    """Preserve the original start time across phase updates so the UI can
    show 'running for 18s'."""
    try:
        if STATE_FILE.exists():
            existing = json.loads(STATE_FILE.read_text())
            if existing.get("pid") == os.getpid() and existing.get("started_at"):
                return existing["started_at"]
    except Exception:
        pass
    return _utc_now_iso()


def clear() -> None:
    """Mark no-task-running. Safe if called when the file is already gone."""
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def read() -> dict | None:
    """Return the current task snapshot, or None if nothing is running."""
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return None


@contextmanager
def task(name: str, detail: str | None = None, run_id: str | None = None) -> Iterator[None]:
    """Convenience context manager: write on enter, clear on exit. Failures
    are still reflected in the heartbeat because runstate is cleared after
    the failure is captured upstream."""
    write(name, detail=detail, run_id=run_id)
    try:
        yield
    finally:
        clear()


def update_phase(phase: str, progress: float | None = None) -> None:
    """Update the running task's phase without touching detail/run_id."""
    cur = read()
    if cur is None:
        return
    cur["phase"] = phase
    cur["progress"] = progress
    cur["updated_at"] = _utc_now_iso()
    _atomic_write(STATE_FILE, json.dumps(cur))
