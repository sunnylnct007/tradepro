"""Process-scoped session trace.

The per-Q&A traces in trace.py only get written when the LLM
cooperates by calling begin_trace / record_step / finalize_trace.
Claude Desktop (and most clients) don't do that for casual chat
turns — so 99% of tool calls were going unrecorded.

This module records every tool and resource invocation regardless
of LLM cooperation, into a single per-process file:

    ~/.tradepro/traces/session-<utc>-<short>.json

Each Claude Desktop launch spawns a fresh MCP process via uv, so
'one session = one process = one file'. The file is rewritten
atomically after every step so a kill -9 still leaves a useful
artefact. Output bodies are truncated (800 chars by default) to
keep the trace readable; set TRADEPRO_TRACE_FULL=1 to capture full
outputs at the cost of larger files.
"""
from __future__ import annotations

import functools
import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .trace import TRACE_ROOT

_LOCK = threading.Lock()
_SESSION: "SessionTrace | None" = None
_TRUNC = 800


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"session-{stamp}-{uuid.uuid4().hex[:6]}"


class SessionTrace:
    """Append-only log of every tool/resource/prompt invocation in
    one MCP server process."""

    def __init__(self) -> None:
        self.session_id = _new_session_id()
        self.started_at = _now_iso()
        self.pid = os.getpid()
        self.steps: list[dict] = []
        self.path = TRACE_ROOT / f"{self.session_id}.json"

    def append(self, **step: Any) -> None:
        with _LOCK:
            self.steps.append(step)
            self._save()

    def _save(self) -> None:
        TRACE_ROOT.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        payload = {
            "kind": "session_trace",
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": _now_iso(),
            "pid": self.pid,
            "step_count": len(self.steps),
            "steps": self.steps,
        }
        tmp.write_text(json.dumps(payload, default=str, ensure_ascii=False, indent=2))
        tmp.replace(self.path)


def session() -> SessionTrace:
    global _SESSION
    if _SESSION is None:
        _SESSION = SessionTrace()
    return _SESSION


def session_path() -> Path:
    return session().path


def instrumented(name: str, kind: str = "tool_call") -> Callable[[Callable], Callable]:
    """Decorator that records inputs, output summary, latency, and
    errors to the session trace. `name` is the public tool/resource
    name; `kind` follows the trace.py taxonomy.

    Use it AFTER the FastMCP registration decorator so the recorded
    name matches what the LLM sees:

        @mcp.tool()
        @instrumented("get_compare")
        def get_compare(universe: str) -> str: ...
    """
    full = os.environ.get("TRADEPRO_TRACE_FULL") == "1"

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            error: str | None = None
            result: Any = None
            try:
                result = fn(*args, **kwargs)
                return result
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"
                raise
            finally:
                t1 = time.perf_counter()
                try:
                    session().append(
                        ts=_now_iso(),
                        kind=kind,
                        name=name,
                        inputs=_capture_inputs(args, kwargs),
                        outputs=_capture_output(result, full=full),
                        error=error,
                        latency_ms=int((t1 - t0) * 1000),
                    )
                except Exception:  # noqa: BLE001
                    # Tracing must never break the tool. Swallow.
                    pass

        return wrapper

    return deco


def _capture_inputs(args: tuple, kwargs: dict) -> dict:
    out: dict[str, Any] = {}
    if args:
        out["args"] = [_truncate(a) for a in args]
    if kwargs:
        out["kwargs"] = {k: _truncate(v) for k, v in kwargs.items()}
    return out


def _capture_output(obj: Any, *, full: bool) -> Any:
    if obj is None:
        return None
    # FastMCP tools return JSON strings — try to parse for a
    # structured summary; fall back to truncated text.
    parsed: Any = None
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except (json.JSONDecodeError, ValueError):
            return _truncate(obj)
    elif isinstance(obj, dict):
        parsed = obj

    if parsed is None:
        return _truncate(str(obj))

    if full:
        return parsed

    # Default: lean summary so the trace stays readable. Keep the
    # citation URI (`_source`), the ok/error flags, and a few common
    # identifiers; otherwise just list the keys.
    keep = (
        "_source", "ok", "error", "fetched_at", "universe", "symbol",
        "row_count", "bars_used", "step_count", "trace_id", "outcome",
        "best_overall", "model", "verdict",
    )
    summary: dict[str, Any] = {}
    if isinstance(parsed, dict):
        for k in keep:
            if k in parsed:
                summary[k] = _truncate(parsed[k])
        if not summary:
            summary["keys"] = list(parsed.keys())[:12]
    else:
        summary["value"] = _truncate(parsed)
    return summary


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _TRUNC else value[:_TRUNC] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_truncate(v) for v in list(value)[:20]]
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in list(value.items())[:20]}
    text = str(value)
    return text if len(text) <= _TRUNC else text[:_TRUNC] + "…"
