"""BarStore — the public entry point for the trustworthy bar cache.

Contract:

    store = BarStore(base_dir=Path("~/.tradepro/bar_cache").expanduser())
    frame = store.get(
        canonical="SPY",
        asset_class="us_etf",
        resolution="1m",
        start=datetime(2024, 12, 23, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
    )
    print(frame.df.shape, frame.coverage_complete)

Guarantees:

    * If the cached partitions for the requested range are complete +
      manifest-validated, returns the cached frame (cache hit).
    * If not, falls through the provider chain in order, writes each
      partition atomically (tmp + fsync + rename) with a manifest,
      revalidates, and returns the result.
    * Raises ``BarFetchError`` (subclass) on any failure that isn't
      recoverable via the chain. Never returns partial data silently.
    * Emits one ``bar_cache_events`` row per call. Updates the
      ``bar_cache_health`` table per-symbol.

Phase B-1 limitations (documented; closed in B-2/3):
    * Provider chain is hardcoded ``["yfinance"]`` here. Phase B-3
      reads ``data_source_preferences`` from Postgres so the chain is
      operator-editable end-to-end. The hardcoded chain ships first
      so the architecture works without any backend wiring.
    * Telemetry DB write is opt-in (caller passes a writer callback).
      JSONL fallback is always on. Phase C wires the DB writer via
      the worker daemon.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .asset_class import AssetClassPlugin, get_asset_class
from .errors import (
    BarFetchError,
    ManifestViolation,
    NoProviderAvailableError,
    ProviderRateLimitError,
    SchemaVersionMismatch,
)
from .manifest import Manifest
from .providers import get_provider
from .telemetry import FetchEvent, NullSink, TelemetrySink


_log = logging.getLogger("tradepro.bar_cache.store")


@dataclass
class BarFrame:
    """Result of a successful ``BarStore.get()``.

    Carries the bars + the provenance the caller needs to reason
    about what they have ("did this come from cache, from yfinance,
    is the coverage complete?"). The strategy code reads ``df``;
    everything else is for the audit trail."""
    df: pd.DataFrame
    coverage_complete: bool
    partitions_used: list[str]
    provider_chain_tried: list[str]
    provider_used: str               # final source ("cache" if all hits)
    rows_returned: int
    rows_expected: int
    schema_version: str
    fetched_at_utc: str
    notes: list[str] = field(default_factory=list)


class BarStore:
    """Composable, asset-class-agnostic bar cache.

    Stateless beyond the base directory + telemetry sink + provider
    chain configuration. Safe to share across threads as long as
    Parquet writes within a partition are serialised (we don't write
    to the same (canonical, asset_class, resolution, partition) tuple
    concurrently — the file-rename is atomic but two racing renames
    would still produce a winner; one writer per partition is the
    contract)."""

    def __init__(
        self,
        base_dir: Path,
        *,
        telemetry: Optional[TelemetrySink] = None,
        provider_chain: Optional[list[str]] = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.telemetry = telemetry or NullSink()
        # Hardcoded chain for B-1 — B-3 reads data_source_preferences.
        self._chain = provider_chain or ["yfinance"]

    # ── Public API ──────────────────────────────────────────────────

    def get(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        start: datetime,
        end: datetime,
        *,
        allow_partial: bool = False,
        force_refresh: bool = False,
        fetched_by: str = "unknown",
    ) -> BarFrame:
        """Fetch bars for the (canonical, resolution, range) tuple.

        ``allow_partial`` is the OPT-IN flag for partial-data
        tolerance. Defaults to False — partial reads are the banned
        behaviour. A strategy that explicitly wants "give me what
        you can" sets it to True and gets back a frame plus the
        ``coverage_complete=False`` flag.

        ``force_refresh`` bypasses the cache check + re-pulls from
        the provider chain regardless of what's on disk. Used by
        the reload op."""
        plugin = get_asset_class(asset_class)
        if resolution not in plugin.supported_resolutions():
            raise BarFetchError(
                error_class="schema",
                provider="bar_cache",
                canonical=canonical,
                message=(
                    f"resolution {resolution!r} not supported by asset "
                    f"class {asset_class!r}; supported: "
                    f"{plugin.supported_resolutions()}"
                ),
                retry_strategy="fatal",
            )

        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)

        # Walk the partitions the range crosses.
        partitions = self._partitions_in_range(plugin, start_utc, end_utc)
        if not partitions:
            return self._empty_result(
                plugin, canonical, asset_class, resolution,
                start_utc, end_utc,
            )

        chain_log: list[str] = []
        provider_used = "cache"
        provider_versions: dict[str, Any] = {}

        t0 = time.perf_counter()

        for partition in partitions:
            partition_path = self._partition_path(
                canonical, asset_class, resolution, partition,
            )
            manifest_path = self._manifest_path(
                canonical, asset_class, resolution, partition,
            )

            need_fetch = force_refresh
            if manifest_path.exists():
                try:
                    manifest = Manifest.read(manifest_path)
                except Exception as exc:  # noqa: BLE001
                    self._raise_manifest_violation(
                        canonical, partition,
                        expected={"manifest_readable": True},
                        actual={"error": str(exc)},
                        chain_log=chain_log, provider_versions=provider_versions,
                        asset_class=asset_class, resolution=resolution,
                        start_utc=start_utc, end_utc=end_utc,
                        plugin=plugin, t0=t0,
                    )
                # Schema version match check
                if manifest.schema_version != plugin.schema.schema_version:
                    self._raise_schema_mismatch(
                        canonical, partition,
                        expected_version=plugin.schema.schema_version,
                        actual_version=manifest.schema_version,
                        chain_log=chain_log, provider_versions=provider_versions,
                        asset_class=asset_class, resolution=resolution,
                        start_utc=start_utc, end_utc=end_utc,
                        plugin=plugin, t0=t0,
                    )
                if manifest.is_complete() and not force_refresh:
                    chain_log.append("cache_hit")
                    continue   # this partition is fine; move on
                else:
                    need_fetch = True
                    chain_log.append("cache_incomplete")
            else:
                # No manifest = cache miss = must fetch
                need_fetch = True
                chain_log.append("cache_miss")

            if need_fetch:
                # Fall through the provider chain for this partition.
                partition_start, partition_end = self._partition_range(
                    plugin, partition,
                )
                self._fetch_and_write(
                    plugin=plugin,
                    canonical=canonical,
                    asset_class=asset_class,
                    resolution=resolution,
                    partition=partition,
                    partition_start=partition_start,
                    partition_end=partition_end,
                    partition_path=partition_path,
                    manifest_path=manifest_path,
                    chain_log=chain_log,
                    provider_versions=provider_versions,
                    fetched_by=fetched_by,
                )
                provider_used = chain_log[-1].split(":", 1)[0] \
                    if ":" in chain_log[-1] else chain_log[-1]

        # All partitions touched (cached or written). Now read the
        # parquet back + slice to the requested range.
        df, partitions_used = self._read_partitions(
            canonical, asset_class, resolution,
            partitions, start_utc, end_utc,
        )

        # Validate post-read coverage.
        rows_expected = self._sum_expected_bar_count(
            plugin, resolution, start_utc, end_utc,
        )
        rows_returned = len(df)
        coverage_complete = rows_returned >= rows_expected and not df.empty

        if not coverage_complete and not allow_partial:
            # Compute structured detail for the error + telemetry.
            sessions_expected = plugin.expected_session_dates(start_utc, end_utc)
            sessions_present = set(df.index.tz_convert("UTC").date) if not df.empty else set()
            missing_sessions = [
                d.isoformat() for d in sessions_expected
                if d not in sessions_present
            ]
            self._emit_event(
                canonical=canonical, asset_class=asset_class,
                resolution=resolution,
                start_utc=start_utc, end_utc=end_utc,
                plugin=plugin,
                result="fetched_partial",
                source_chain=chain_log,
                provider_used=provider_used,
                provider_versions=provider_versions,
                rows_expected=rows_expected,
                rows_returned=rows_returned,
                gaps_detected_count=len(missing_sessions),
                latency_ms=_ms(t0),
            )
            raise BarFetchError(
                error_class="partial_coverage",
                provider="bar_cache",
                canonical=canonical,
                message=(
                    f"partial coverage for {canonical}/{asset_class}/{resolution} "
                    f"{start_utc.date()} → {end_utc.date()}: "
                    f"got {rows_returned} of expected {rows_expected} bars, "
                    f"missing sessions: {missing_sessions[:5]}"
                    + ("..." if len(missing_sessions) > 5 else "")
                ),
                expected={
                    "rows": rows_expected,
                    "sessions": [d.isoformat() for d in sessions_expected],
                },
                actual={
                    "rows": rows_returned,
                    "missing_sessions": missing_sessions,
                },
                retry_strategy="user_intervention",
            )

        result_kind = "complete" if "cache_hit" in chain_log and "cache_miss" not in chain_log else (
            "fetched_complete" if coverage_complete else "fetched_partial"
        )
        # Gap counting — useful for the cockpit dashboard even when
        # the caller opted into allow_partial. Recompute here because
        # the failure path's identical computation lives behind the
        # ``not allow_partial`` branch above.
        if not coverage_complete and not df.empty:
            sessions_expected = plugin.expected_session_dates(start_utc, end_utc)
            sessions_present = set(df.index.tz_convert("UTC").date)
            gap_count = sum(
                1 for d in sessions_expected if d not in sessions_present
            )
        elif not coverage_complete:
            sessions_expected = plugin.expected_session_dates(start_utc, end_utc)
            gap_count = len(sessions_expected)
        else:
            gap_count = 0
        self._emit_event(
            canonical=canonical, asset_class=asset_class,
            resolution=resolution,
            start_utc=start_utc, end_utc=end_utc,
            plugin=plugin,
            result=result_kind,
            source_chain=chain_log,
            provider_used=provider_used,
            provider_versions=provider_versions,
            rows_expected=rows_expected,
            rows_returned=rows_returned,
            gaps_detected_count=gap_count,
            latency_ms=_ms(t0),
        )

        return BarFrame(
            df=df,
            coverage_complete=coverage_complete,
            partitions_used=partitions_used,
            provider_chain_tried=list(chain_log),
            provider_used=provider_used,
            rows_returned=rows_returned,
            rows_expected=rows_expected,
            schema_version=plugin.schema.schema_version,
            fetched_at_utc=Manifest.now_iso(),
            notes=[],
        )

    # ── Internals ───────────────────────────────────────────────────

    def _fetch_and_write(
        self, *,
        plugin: AssetClassPlugin,
        canonical: str,
        asset_class: str,
        resolution: str,
        partition: str,
        partition_start: datetime,
        partition_end: datetime,
        partition_path: Path,
        manifest_path: Path,
        chain_log: list[str],
        provider_versions: dict[str, Any],
        fetched_by: str,
    ) -> None:
        """Walk the provider chain until one succeeds. Write the
        parquet + manifest atomically. Caller catches the
        chain-exhausted case."""
        last_exc: Optional[BarFetchError] = None
        attempted: list[str] = []

        for provider_name in self._chain:
            try:
                provider = get_provider(provider_name)
            except KeyError:
                chain_log.append(f"{provider_name}_unknown")
                continue
            if not provider.supports_resolution(resolution):
                chain_log.append(f"{provider_name}_unsupported")
                continue
            attempted.append(provider_name)
            try:
                df, meta = provider.fetch(
                    canonical=canonical,
                    asset_class=asset_class,
                    resolution=resolution,
                    start=partition_start,
                    end=partition_end,
                )
            except BarFetchError as exc:
                last_exc = exc
                chain_log.append(f"{provider_name}_{exc.error_class}")
                continue

            # Validate before write so a bad frame doesn't pollute disk.
            try:
                plugin.validate_frame(df)
            except BarFetchError as exc:
                last_exc = exc
                chain_log.append(f"{provider_name}_parse")
                continue

            # Atomic write.
            self._write_partition(
                df=df,
                plugin=plugin,
                canonical=canonical,
                asset_class=asset_class,
                resolution=resolution,
                partition=partition,
                partition_start=partition_start,
                partition_end=partition_end,
                partition_path=partition_path,
                manifest_path=manifest_path,
                provider_used=provider_name,
                provider_meta=meta,
                fetched_by=fetched_by,
            )
            chain_log.append(f"{provider_name}_ok")
            provider_versions[provider_name] = meta.get("provider_version", "")
            return

        # Chain exhausted.
        raise NoProviderAvailableError(
            canonical=canonical,
            asset_class=asset_class,
            resolution=resolution,
            attempted=attempted,
        ) from last_exc

    def _write_partition(
        self, *,
        df: pd.DataFrame,
        plugin: AssetClassPlugin,
        canonical: str,
        asset_class: str,
        resolution: str,
        partition: str,
        partition_start: datetime,
        partition_end: datetime,
        partition_path: Path,
        manifest_path: Path,
        provider_used: str,
        provider_meta: dict[str, Any],
        fetched_by: str,
    ) -> None:
        """tmp + fsync + rename for the parquet, then for the manifest.
        Never leaves a partial file under the partition path."""
        partition_path.parent.mkdir(parents=True, exist_ok=True)

        # Filter dataframe to the partition window — provider might
        # have over-fetched slightly at the edges.
        if not df.empty:
            df = df[(df.index >= partition_start) & (df.index < partition_end)]

        # Write parquet to tmp + atomic rename.
        tmp_parquet = partition_path.with_suffix(partition_path.suffix + ".tmp")
        if not df.empty:
            # Ensure column order matches the schema for stable reads.
            ordered = [c for c in plugin.schema.column_order if c in df.columns]
            extras = [c for c in df.columns if c not in ordered]
            df_to_write = df[ordered + extras]
            table = pa.Table.from_pandas(df_to_write, preserve_index=True)
            pq.write_table(table, tmp_parquet, compression="zstd")
            # fsync the file then the directory so the rename is durable.
            fd = os.open(tmp_parquet, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            tmp_parquet.replace(partition_path)
        else:
            # Empty frame — write a zero-row parquet so the manifest
            # has something to point at + cache hits don't refetch.
            empty_table = pa.Table.from_pandas(
                pd.DataFrame(columns=list(plugin.schema.column_order)),
            )
            pq.write_table(empty_table, tmp_parquet, compression="zstd")
            tmp_parquet.replace(partition_path)

        # Build manifest from the observed result.
        expected_sessions = plugin.expected_session_dates(
            partition_start, partition_end,
        )
        expected_count = sum(
            plugin.expected_bar_count(resolution, d) for d in expected_sessions
        )
        actual_session_dates = sorted({
            d.isoformat() for d in (df.index.tz_convert("UTC").date if not df.empty else [])
        })
        manifest = Manifest(
            schema_version=plugin.schema.schema_version,
            canonical=canonical,
            asset_class=asset_class,
            resolution=resolution,
            partition=partition,
            expected_bar_count=expected_count,
            expected_session_dates=[d.isoformat() for d in expected_sessions],
            actual_bar_count=int(len(df)),
            actual_session_dates=actual_session_dates,
            provider_chain=list(self._chain),
            provider_used=provider_used,
            fetched_at_utc=Manifest.now_iso(),
            fetched_by=fetched_by,
            file_relative_path=str(
                partition_path.relative_to(self.base_dir)
            ),
            file_size_bytes=partition_path.stat().st_size,
            notes=str(provider_meta.get("interval", "")),
        )
        manifest.write(manifest_path)

    def _read_partitions(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        partitions: list[str],
        start_utc: datetime,
        end_utc: datetime,
    ) -> tuple[pd.DataFrame, list[str]]:
        """Concatenate the requested partitions + slice to range.
        Returns (frame, partitions_actually_read)."""
        frames: list[pd.DataFrame] = []
        actually_read: list[str] = []
        for partition in partitions:
            path = self._partition_path(
                canonical, asset_class, resolution, partition,
            )
            if not path.exists():
                continue
            table = pq.read_table(path)
            df = table.to_pandas()
            if df.empty:
                actually_read.append(partition)
                continue
            # Restore the index from the timestamp column if needed.
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            else:
                df.index = df.index.tz_convert("UTC")
            frames.append(df)
            actually_read.append(partition)
        if not frames:
            return pd.DataFrame(), actually_read
        out = pd.concat(frames, axis=0).sort_index()
        out = out[(out.index >= start_utc) & (out.index < end_utc)]
        return out, actually_read

    # ── Path helpers ─────────────────────────────────────────────────

    def _partition_path(
        self, canonical: str, asset_class: str,
        resolution: str, partition: str,
    ) -> Path:
        return (
            self.base_dir / asset_class / canonical / resolution
            / f"{partition}.parquet"
        )

    def _manifest_path(
        self, canonical: str, asset_class: str,
        resolution: str, partition: str,
    ) -> Path:
        return (
            self.base_dir / asset_class / canonical / resolution
            / f"{partition}.manifest.json"
        )

    @staticmethod
    def _partitions_in_range(
        plugin: AssetClassPlugin,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[str]:
        """Months crossed by ``[start, end]``. Iterates month-by-month
        so multi-year ranges work without per-day stepping."""
        out: list[str] = []
        cur = datetime(start_utc.year, start_utc.month, 1, tzinfo=timezone.utc)
        last = datetime(end_utc.year, end_utc.month, 1, tzinfo=timezone.utc)
        while cur <= last:
            out.append(plugin.partition_key(cur))
            # Next month
            if cur.month == 12:
                cur = cur.replace(year=cur.year + 1, month=1)
            else:
                cur = cur.replace(month=cur.month + 1)
        return out

    @staticmethod
    def _partition_range(
        plugin: AssetClassPlugin, partition: str,
    ) -> tuple[datetime, datetime]:
        """Returns (start_inclusive, end_exclusive) for a partition
        key. Assumes year-month partitioning (us_etf default); when
        future plugins use a different partition strategy they can
        override this on the plugin itself."""
        year, month = (int(x) for x in partition.split("-"))
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        return start, end

    @staticmethod
    def _sum_expected_bar_count(
        plugin: AssetClassPlugin,
        resolution: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> int:
        sessions = plugin.expected_session_dates(start_utc, end_utc)
        return sum(plugin.expected_bar_count(resolution, d) for d in sessions)

    # ── Telemetry helpers ──────────────────────────────────────────

    def _empty_result(
        self, plugin: AssetClassPlugin, canonical: str,
        asset_class: str, resolution: str,
        start_utc: datetime, end_utc: datetime,
    ) -> BarFrame:
        self._emit_event(
            canonical=canonical, asset_class=asset_class,
            resolution=resolution,
            start_utc=start_utc, end_utc=end_utc, plugin=plugin,
            result="complete",
            source_chain=["range_empty"],
            provider_used="bar_cache",
            provider_versions={},
            rows_expected=0,
            rows_returned=0,
            latency_ms=0,
        )
        return BarFrame(
            df=pd.DataFrame(),
            coverage_complete=True,
            partitions_used=[],
            provider_chain_tried=["range_empty"],
            provider_used="bar_cache",
            rows_returned=0,
            rows_expected=0,
            schema_version=plugin.schema.schema_version,
            fetched_at_utc=Manifest.now_iso(),
        )

    def _emit_event(
        self, *,
        canonical: str, asset_class: str, resolution: str,
        start_utc: datetime, end_utc: datetime,
        plugin: AssetClassPlugin,
        result: str,
        source_chain: list[str],
        provider_used: str,
        provider_versions: dict[str, Any],
        rows_expected: Optional[int],
        rows_returned: Optional[int],
        latency_ms: int,
        gaps_detected_count: int = 0,
        error_class: Optional[str] = None,
        error_provider: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_strategy: Optional[str] = None,
    ) -> None:
        event = FetchEvent(
            canonical=canonical,
            asset_class=asset_class,
            resolution=resolution,
            range_start_utc=start_utc,
            range_end_utc=end_utc,
            result=result,
            source_chain=source_chain,
            provider_used=provider_used,
            provider_versions=provider_versions,
            rows_expected=rows_expected,
            rows_returned=rows_returned,
            gaps_detected_count=gaps_detected_count,
            schema_version=plugin.schema.schema_version,
            latency_ms=latency_ms,
            error_class=error_class,
            error_provider=error_provider,
            error_message=error_message,
            retry_strategy=retry_strategy,
        )
        try:
            self.telemetry.emit(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning("telemetry emit failed (continuing): %s", exc)

    def _raise_manifest_violation(
        self, canonical: str, partition: str,
        expected: dict[str, Any], actual: dict[str, Any],
        chain_log: list[str], provider_versions: dict[str, Any],
        asset_class: str, resolution: str,
        start_utc: datetime, end_utc: datetime,
        plugin: AssetClassPlugin, t0: float,
    ) -> None:
        self._emit_event(
            canonical=canonical, asset_class=asset_class, resolution=resolution,
            start_utc=start_utc, end_utc=end_utc, plugin=plugin,
            result="manifest_violation",
            source_chain=chain_log,
            provider_used="bar_cache",
            provider_versions=provider_versions,
            rows_expected=None,
            rows_returned=None,
            latency_ms=_ms(t0),
            error_class="manifest",
            error_provider="bar_cache",
            error_message=f"expected {expected}, actual {actual}",
            retry_strategy="user_intervention",
        )
        raise ManifestViolation(
            canonical=canonical, partition=partition,
            expected=expected, actual=actual,
        )

    def _raise_schema_mismatch(
        self, canonical: str, partition: str,
        expected_version: str, actual_version: str,
        chain_log: list[str], provider_versions: dict[str, Any],
        asset_class: str, resolution: str,
        start_utc: datetime, end_utc: datetime,
        plugin: AssetClassPlugin, t0: float,
    ) -> None:
        self._emit_event(
            canonical=canonical, asset_class=asset_class, resolution=resolution,
            start_utc=start_utc, end_utc=end_utc, plugin=plugin,
            result="manifest_violation",
            source_chain=chain_log,
            provider_used="bar_cache",
            provider_versions=provider_versions,
            rows_expected=None,
            rows_returned=None,
            latency_ms=_ms(t0),
            error_class="schema",
            error_provider="bar_cache",
            error_message=(
                f"expected schema {expected_version!r}, "
                f"got {actual_version!r}"
            ),
            retry_strategy="user_intervention",
        )
        raise SchemaVersionMismatch(
            canonical=canonical, partition=partition,
            expected_version=expected_version,
            actual_version=actual_version,
        )


# ── Module helpers ─────────────────────────────────────────────────


def _ensure_utc(ts: datetime) -> datetime:
    """Normalise a datetime to tz-aware UTC. Naive input is assumed
    UTC (consistent with how the rest of the project handles
    timestamps — UTC everywhere)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
