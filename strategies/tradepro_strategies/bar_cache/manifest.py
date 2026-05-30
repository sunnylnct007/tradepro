"""Manifest for a cached partition.

Each Parquet partition under ``<base>/<asset_class>/<canonical>/<resolution>/``
ships with a sidecar JSON manifest declaring what's expected to be in it.
The reader compares actual vs declared on every read; mismatches raise
``ManifestViolation``. Silent partial reads are the banned behaviour —
the manifest is what makes that guarantee enforceable.

Manifest schema is intentionally explicit + JSON: a future investigator
can ``cat`` one and understand what was supposed to be in the partition
without running Python.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Manifest:
    """Declaration of what a partition should contain.

    Fields are deliberately verbose for human-readability — a manifest
    on disk should answer "what did we ask for, what did we get, when,
    from whom" without context.
    """
    # Schema
    schema_version: str             # e.g. "us_equity_v1"

    # Identity
    canonical: str                  # e.g. "SPY"
    asset_class: str                # e.g. "us_etf"
    resolution: str                 # e.g. "1m"
    partition: str                  # e.g. "2024-12" (month) — owned by AssetClass

    # Expectation declared at write time
    expected_bar_count: int
    expected_session_dates: list[str]   # ISO date strings

    # Actuals observed when the parquet was written
    actual_bar_count: int
    actual_session_dates: list[str]

    # Provenance
    provider_chain: list[str]       # what was tried, in order
    provider_used: str              # the one that answered
    fetched_at_utc: str             # ISO datetime
    fetched_by: str                 # cli / strategy / etc.

    # File details — useful for cheap integrity checks
    file_relative_path: str         # relative to the cache base dir
    file_size_bytes: int

    # Optional notes the operator / cache writer left
    notes: str = ""

    # ── Derived predicates the reader uses ──────────────────────────

    def is_complete(self) -> bool:
        """True when actual coverage matches expected coverage. Used
        as the "should I trust this partition?" gate."""
        return (
            self.actual_bar_count >= self.expected_bar_count
            and not self.missing_session_dates()
        )

    def missing_session_dates(self) -> list[str]:
        """Sessions the partition was meant to cover but didn't.
        Empty list when complete."""
        expected = set(self.expected_session_dates)
        actual = set(self.actual_session_dates)
        return sorted(expected - actual)

    # ── Persistence ─────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Manifest":
        # Tolerant deserialisation: unknown fields ignored (forward
        # compatibility), missing fields default. A schema bump should
        # always go through SchemaVersionMismatch, not silent drift.
        return cls(
            schema_version=str(d.get("schema_version", "")),
            canonical=str(d.get("canonical", "")),
            asset_class=str(d.get("asset_class", "")),
            resolution=str(d.get("resolution", "")),
            partition=str(d.get("partition", "")),
            expected_bar_count=int(d.get("expected_bar_count", 0)),
            expected_session_dates=list(d.get("expected_session_dates", [])),
            actual_bar_count=int(d.get("actual_bar_count", 0)),
            actual_session_dates=list(d.get("actual_session_dates", [])),
            provider_chain=list(d.get("provider_chain", [])),
            provider_used=str(d.get("provider_used", "")),
            fetched_at_utc=str(d.get("fetched_at_utc", "")),
            fetched_by=str(d.get("fetched_by", "")),
            file_relative_path=str(d.get("file_relative_path", "")),
            file_size_bytes=int(d.get("file_size_bytes", 0)),
            notes=str(d.get("notes", "")),
        )

    def write(self, path: Path) -> None:
        """Atomic write — same pattern as Parquet writes. tmp + rename.
        Manifest is small so we don't fsync explicitly; the tmp+rename
        protects against partial JSON."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.write("\n")
        tmp.replace(path)

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        with path.open("r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @staticmethod
    def now_iso() -> str:
        """ISO 8601 UTC timestamp suitable for the ``fetched_at_utc``
        field. Centralised so every manifest uses the same format."""
        return datetime.now(timezone.utc).isoformat()
