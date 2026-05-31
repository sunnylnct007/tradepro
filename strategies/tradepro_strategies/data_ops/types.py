"""Typed request / result for data ops.

Every handler signature is ``handle(request, storage) -> DataOpResult``.
This gives the polling loop a single uniform shape to serialise back
to the API's ``result_summary`` field without per-handler special
casing.

The ``params`` field on the request is intentionally free-form (JSONB
on the backend) because op kinds differ — ``data_backfill`` needs a
date range, ``data_validate`` doesn't. Each handler validates its
own params via dataclass / Pydantic-style checks inside ``handle()``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass(frozen=True)
class DataOpRequest:
    """One unit of work the worker claimed. Mirrors the backend's
    session_requests row (request_id + kind + params); the worker
    constructs it from the poll response."""
    request_id: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class DataOpResult:
    """Handler return value, normalised across every op kind.

    ``ok`` is the worker's gate: True → POST status="completed", False
    → POST status="failed" with ``error`` as the message. ``summary``
    is a short human-readable line for the cockpit (single-line);
    ``detail`` is the full structured report (per-partition stats,
    per-resolution gaps, etc.) that the cockpit drill-down uses.

    Keeping these separate lets the cockpit's recent-events list show
    "12 partitions complete + 3 incomplete" without parsing the full
    detail blob; the drill-in then loads ``detail`` for the table."""
    ok: bool
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    completed_at_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_wire_dict(self) -> dict[str, Any]:
        """Shape sent back to /api/ops/complete-data/{id} as
        ``result_summary``. Backend stores it as JSONB; the cockpit
        reads it back as a plain JSON object."""
        return {
            "ok": self.ok,
            "summary": self.summary,
            "detail": self.detail,
            "error": self.error,
            "completed_at_utc": self.completed_at_utc,
        }
