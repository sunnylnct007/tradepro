"""Backtest preflight — Phase D-3 (auto-stamping) + Phase E (hard-block).

Backtest CLIs call ``preflight_data_state`` before running. It:

  1. Calls BarStore.get for the requested (canonical, asset_class,
     resolution, start, end) tuple — populating the cache if needed.
  2. Returns a dict the caller stamps on the backtest report:
        data_state_hash, coverage_complete, rows_returned, rows_expected,
        partitions_used, provider_used, schema_version, base_dir.
  3. Refuses the run with a structured ``IncompleteDataError`` when
     coverage is incomplete and ``require_complete`` is True. (Phase E
     "stop pretending" guarantee.) The exception message tells the
     operator what's missing + how to fix it (run a backfill).

Two args control behaviour:
  * ``require_complete=True`` (default) — incomplete data is a hard
    failure. The caller MAY override per backtest with the legacy
    ``--allow-incomplete-data`` flag.
  * ``api_base`` — optional, lights up BackendTelemetrySink +
    PreferencesLoader. Without it the preflight still works (JSONL
    telemetry, hardcoded chain).

Producer-side automatic hashing — every backtest CLI that calls this
gets reproducibility for free, no per-caller stamping logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class IncompleteDataError(Exception):
    """Raised when ``require_complete=True`` AND the BarStore
    reports partial coverage. The message includes a backfill hint
    so the operator can self-serve. The structured fields let a CLI
    caller render the error however it wants (JSON for CI, pretty
    for humans)."""

    def __init__(
        self,
        canonical: str, asset_class: str, resolution: str,
        rows_expected: int, rows_returned: int,
        missing_sessions: list[str],
    ) -> None:
        self.canonical = canonical
        self.asset_class = asset_class
        self.resolution = resolution
        self.rows_expected = rows_expected
        self.rows_returned = rows_returned
        self.missing_sessions = missing_sessions
        super().__init__(
            f"Backtest refuses to run on incomplete data for "
            f"{canonical}/{asset_class}/{resolution}: "
            f"{rows_returned} of {rows_expected} bars "
            f"({len(missing_sessions)} sessions missing). "
            f"Fix: run a backfill from the cockpit (Settings → Bar "
            f"cache activity → Backfill) or via CLI: "
            f"tradepro-bar-cache-get --canonical {canonical} "
            f"--asset {asset_class} --resolution {resolution} "
            f"--from <start> --to <end>. "
            f"To override: pass allow_incomplete=True."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "incomplete_data",
            "canonical": self.canonical,
            "asset_class": self.asset_class,
            "resolution": self.resolution,
            "rows_expected": self.rows_expected,
            "rows_returned": self.rows_returned,
            "missing_sessions": list(self.missing_sessions),
            "remediation": (
                "Run a backfill from the cockpit or via "
                "tradepro-bar-cache-get."
            ),
        }


@dataclass(frozen=True)
class PreflightResult:
    """What the preflight returns to the caller for stamping on
    the report payload. ``data_state`` is the field the backtest
    pushes through to the backend."""
    data_state_hash: str
    coverage_complete: bool
    rows_returned: int
    rows_expected: int
    partitions_used: list[str]
    provider_used: str
    schema_version: str
    base_dir: str
    # Fail-VISIBLE data-quality: when the run was allowed to proceed on
    # slightly-incomplete data (missing_fraction ≤ tolerance), the gap is NOT
    # hidden — it's reported here so the report/UI can surface "ran on 99.9%
    # coverage, 1 session missing" rather than a silent pass.
    missing_sessions: list[str] = field(default_factory=list)
    missing_fraction: float = 0.0

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "data_state_hash": self.data_state_hash,
            "data_state": {
                "hash": self.data_state_hash,
                "coverage_complete": self.coverage_complete,
                "rows_returned": self.rows_returned,
                "rows_expected": self.rows_expected,
                "partitions_used": list(self.partitions_used),
                "provider_used": self.provider_used,
                "schema_version": self.schema_version,
                "base_dir": self.base_dir,
                "missing_sessions": list(self.missing_sessions),
                "missing_fraction": self.missing_fraction,
            },
        }


def preflight_data_state(
    canonical: str,
    asset_class: str,
    resolution: str,
    start: datetime,
    end: datetime,
    *,
    require_complete: bool = True,
    max_missing_fraction: float = 0.0,
    api_base: str | None = None,
    auth_token: str | None = None,
    base_dir: Path | None = None,
    fetched_by: str = "preflight",
) -> PreflightResult:
    """Wraps BarStore.get with the auto-stamp + hard-block contract.

    Returns a ``PreflightResult`` on success (caller stamps it on the
    backtest report payload). Raises ``IncompleteDataError`` when
    coverage is incomplete AND ``require_complete=True`` AND the missing
    fraction exceeds ``max_missing_fraction``.

    ``max_missing_fraction`` (default 0.0 = strict) lets a backtest run on
    near-complete data — e.g. 0.01 tolerates up to 1% of sessions missing
    (an isolated harvest gap) WITHOUT silently hiding it: the missing
    sessions + fraction are recorded on the result so the report stays
    honest. A larger hole still hard-fails. This is the middle ground
    between "abort on one missing bar" and the blunt allow_incomplete
    override.

    On a BarStore-level failure (no provider, schema mismatch, etc.),
    re-raises the underlying ``BarFetchError`` — backtests should
    refuse to run rather than silently use stale fallback data."""
    # Late imports — keeps the module cheap to import for callers
    # that don't actually need to preflight (e.g. tests / --help).
    from .asset_classes import UsEtfPlugin  # noqa: F401
    from .preferences import PreferencesLoader
    from .providers import YFinanceProvider  # noqa: F401
    from .store import BarStore
    from .telemetry import BackendTelemetrySink, TelemetrySink

    base = (
        Path(base_dir).expanduser()
        if base_dir
        else Path.home() / ".tradepro" / "bar_cache"
    )
    base.mkdir(parents=True, exist_ok=True)

    if api_base:
        telemetry: TelemetrySink = BackendTelemetrySink(
            base_dir=base, api_base=api_base, auth_token=auth_token,
        )
        preferences_loader: PreferencesLoader | None = PreferencesLoader(
            api_base=api_base, auth_token=auth_token,
        )
    else:
        telemetry = TelemetrySink(base_dir=base)
        preferences_loader = None

    store = BarStore(
        base_dir=base,
        telemetry=telemetry,
        preferences_loader=preferences_loader,
    )

    # Always opt into partial reads at the BarStore level — we'll
    # do the require-complete check ourselves so we can raise a
    # backtest-specific error with the right remediation hint.
    frame = store.get(
        canonical=canonical,
        asset_class=asset_class,
        resolution=resolution,
        start=start, end=end,
        allow_partial=True,
        fetched_by=fetched_by,
    )

    missing: list[str] = []
    missing_fraction = 0.0
    if not frame.coverage_complete:
        from .asset_class import get_asset_class
        plugin = get_asset_class(asset_class)
        sessions_expected = plugin.expected_session_dates(start, end)
        sessions_present = (
            set(frame.df.index.tz_convert("UTC").date)
            if not frame.df.empty else set()
        )
        missing = [
            d.isoformat() for d in sessions_expected
            if d not in sessions_present
        ]
        denom = max(len(sessions_expected), frame.rows_expected, 1)
        missing_fraction = len(missing) / denom
        # Hard-fail only when strict AND the hole exceeds tolerance. A small
        # isolated gap (≤ tolerance) proceeds, but is recorded on the result
        # (fail-visible) — never silently passed.
        if require_complete and missing_fraction > max_missing_fraction:
            raise IncompleteDataError(
                canonical=canonical,
                asset_class=asset_class,
                resolution=resolution,
                rows_expected=frame.rows_expected,
                rows_returned=frame.rows_returned,
                missing_sessions=missing,
            )

    return PreflightResult(
        data_state_hash=frame.data_state_hash,
        coverage_complete=frame.coverage_complete,
        rows_returned=frame.rows_returned,
        rows_expected=frame.rows_expected,
        partitions_used=list(frame.partitions_used),
        provider_used=frame.provider_used,
        schema_version=frame.schema_version,
        base_dir=str(base),
        missing_sessions=missing,
        missing_fraction=missing_fraction,
    )
