"""Structured per-fetch telemetry.

Every BarStore.get() call emits one event. The event goes to:
  1. The Postgres ``bar_cache_events`` table (migration 031) if a
     DB connection is configured.
  2. A local JSONL file at ``<base>/events/<YYYY-MM-DD>.jsonl`` as
     a recovery path — operator can ``cat`` the file even if the
     DB is down. The local file is best-effort; loss isn't fatal.

We never fail a fetch because telemetry failed. The cache's job is
to serve bars; the event log is for the cockpit's data-health panel,
not for correctness.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


_log = logging.getLogger("tradepro.bar_cache.telemetry")


@dataclass
class FetchEvent:
    """One row in bar_cache_events. Mirrors the migration 031 schema
    column-for-column so the writer can pivot fields straight in."""
    canonical: str
    asset_class: str
    resolution: str
    range_start_utc: datetime
    range_end_utc: datetime
    result: str                                  # "complete" | "fetched_complete" | ...
    source_chain: list[str] = field(default_factory=list)
    provider_used: Optional[str] = None
    provider_versions: dict[str, Any] = field(default_factory=dict)
    rows_expected: Optional[int] = None
    rows_returned: Optional[int] = None
    gaps_detected_count: int = 0
    schema_version: str = ""
    latency_ms: int = 0
    error_class: Optional[str] = None
    error_provider: Optional[str] = None
    error_message: Optional[str] = None
    retry_strategy: Optional[str] = None

    def to_jsonl_row(self) -> str:
        """One-line JSON for the JSONL recovery log."""
        return json.dumps(self.to_dict(), default=str)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # JSON can't serialize datetimes; coerce.
        for k in ("range_start_utc", "range_end_utc"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        d["occurred_at_utc"] = datetime.now(timezone.utc).isoformat()
        return d


class TelemetrySink:
    """Where fetch events get written. Two backends:
      * JSONL append (always tried; best-effort)
      * Postgres INSERT (optional; configured via db_writer callback)
    Either can fail without breaking the fetch."""

    def __init__(
        self,
        base_dir: Path,
        *,
        db_writer: Optional[Callable[[FetchEvent], None]] = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self._db_writer = db_writer
        # Lazy: directory is created on first emit so a read-only
        # test environment doesn't crash on construction.

    def emit(self, event: FetchEvent) -> None:
        # Try DB first; failure is non-fatal.
        if self._db_writer is not None:
            try:
                self._db_writer(event)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "telemetry DB write failed (continuing): %s", exc
                )
        # JSONL fallback always runs so we have a local audit trail.
        try:
            self._append_jsonl(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "telemetry JSONL write failed (giving up): %s", exc
            )

    def _append_jsonl(self, event: FetchEvent) -> None:
        events_dir = self.base_dir / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = events_dir / f"{day}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(event.to_jsonl_row())
            f.write("\n")


class NullSink(TelemetrySink):
    """No-op sink for tests that don't care about telemetry. Avoids
    touching disk."""

    def __init__(self) -> None:  # type: ignore[override]
        # Skip parent init — don't take a base_dir, don't lazy-create.
        self._db_writer = None
        self.base_dir = Path(".")
        self._events: list[FetchEvent] = []

    def emit(self, event: FetchEvent) -> None:  # type: ignore[override]
        self._events.append(event)

    @property
    def events(self) -> list[FetchEvent]:
        return self._events


class BackendTelemetrySink(TelemetrySink):
    """Sink that POSTs each event to the backend's
    /api/admin/data-trust/bar-cache/events endpoint, then falls
    through to the JSONL append as a recovery path. Used by the
    operator CLI when --api-base is provided.

    Best-effort by design — a 4xx/5xx from the backend is logged
    and ignored. The fetch never fails because telemetry failed."""

    def __init__(
        self,
        base_dir: Path,
        api_base: str,
        *,
        auth_token: Optional[str] = None,
        timeout_seconds: float = 5.0,
        _http_post: Optional[Callable[..., Any]] = None,
    ) -> None:
        # Compose on top of the parent's JSONL behaviour so the local
        # log is always written even when the POST succeeds.
        super().__init__(base_dir=base_dir, db_writer=None)
        self._api_base = api_base.rstrip("/")
        self._auth_token = auth_token
        self._timeout = timeout_seconds
        self._http_post = _http_post  # for tests

    def emit(self, event: FetchEvent) -> None:
        # Try the POST first; whatever happens, append the JSONL.
        try:
            self._post(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "telemetry POST failed (continuing): %s", exc,
            )
        # Re-use parent's JSONL append.
        try:
            self._append_jsonl(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "telemetry JSONL write failed (giving up): %s", exc,
            )

    def _post(self, event: FetchEvent) -> None:
        url = f"{self._api_base}/api/admin/data-trust/bar-cache/events"
        body = self._event_to_payload(event)
        headers = {"content-type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        if self._http_post is not None:
            self._http_post(url, json=body, headers=headers, timeout=self._timeout)
            return
        # Late import so tests can substitute requests-free.
        import requests
        resp = requests.post(
            url, json=body, headers=headers, timeout=self._timeout,
        )
        # 4xx/5xx logged but not raised — best-effort.
        if not resp.ok:
            _log.warning(
                "telemetry POST returned %s: %s",
                resp.status_code, resp.text[:200],
            )

    @staticmethod
    def _event_to_payload(event: FetchEvent) -> dict[str, Any]:
        """Map the FetchEvent dataclass to the BarCacheEventBody DTO
        the backend endpoint expects. JSON-friendly types only."""
        return {
            "canonical": event.canonical,
            "assetClass": event.asset_class,
            "resolution": event.resolution,
            "rangeStartUtc": event.range_start_utc.isoformat(),
            "rangeEndUtc": event.range_end_utc.isoformat(),
            "result": event.result,
            "sourceChain": list(event.source_chain),
            "providerUsed": event.provider_used,
            "providerVersions": event.provider_versions,
            "rowsExpected": event.rows_expected,
            "rowsReturned": event.rows_returned,
            "gapsDetectedCount": event.gaps_detected_count,
            "schemaVersion": event.schema_version,
            "latencyMs": event.latency_ms,
            "errorClass": event.error_class,
            "errorProvider": event.error_provider,
            "errorMessage": event.error_message,
            "retryStrategy": event.retry_strategy,
        }
