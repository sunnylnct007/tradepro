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
from datetime import date as _date_cls
from datetime import datetime, timedelta, timezone
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
from .hashing import (
    EMPTY_DATA_STATE_HASH,
    PartitionFingerprint,
    compute_data_state_hash,
    fingerprint_from_manifest,
)
from .manifest import Manifest
from .preferences import PreferencesLoader
from .providers import get_provider
from .quality import _GOLDEN_BAR_SOURCES
from .telemetry import FetchEvent, NullSink, TelemetrySink


_log = logging.getLogger("tradepro.bar_cache.store")


@dataclass
class BarFrame:
    """Result of a successful ``BarStore.get()``.

    Carries the bars + the provenance the caller needs to reason
    about what they have ("did this come from cache, from yfinance,
    is the coverage complete?"). The strategy code reads ``df``;
    everything else is for the audit trail.

    ``data_state_hash`` (Phase D-1) is a SHA256 hex digest derived
    from the manifests of every partition read. Two BarStore.get()
    calls over the same range that hit the same on-disk parquets
    produce the SAME hash. A future backtest stamped with the hash
    can be replayed knowing the data was identical. See
    ``bar_cache.hashing`` for what's in the hash + why. The sentinel
    string ``EMPTY_DATA_STATE`` (exported from hashing.py) means
    "no partitions were touched"."""
    df: pd.DataFrame
    coverage_complete: bool
    partitions_used: list[str]
    provider_chain_tried: list[str]
    provider_used: str               # final source ("cache" if all hits)
    rows_returned: int
    rows_expected: int
    schema_version: str
    fetched_at_utc: str
    # Phase D-1: deterministic hash of the data state. Reproducibility
    # check for backtests + walk-forwards. See bar_cache.hashing.
    data_state_hash: str = ""
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
        preferences_loader: Optional[PreferencesLoader] = None,
    ) -> None:
        """Args:
            base_dir: where Parquet partitions + manifests live.
            telemetry: where fetch events are emitted (default NullSink).
            provider_chain: hardcoded fallback used when no preference
                is configured for a tuple, or when preferences can't be
                loaded. Default ``["yfinance"]`` matches Phase B-1.
            preferences_loader: when provided, the chain for each
                (asset_class, resolution) is resolved per-call from the
                backend's data_source_preferences table. Cached inside
                the loader with a short TTL so the BarStore doesn't
                HTTP every call. The loader is non-fatal — if it can't
                deliver a chain for a tuple, the BarStore uses the
                ``provider_chain`` fallback.
        """
        self.base_dir = Path(base_dir)
        self.telemetry = telemetry or NullSink()
        # Default chain when the preferences loader is absent or
        # silent for a tuple.
        self._default_chain = provider_chain or ["yfinance"]
        self._preferences_loader = preferences_loader
        # SHARED bar cache: S3 is the source of truth (harvest once, read
        # everywhere); the local dir is a read-through cache. None = pure local
        # (dev). Fail-safe — an S3 hiccup never breaks read/write (see
        # s3_mirror). Single switch: TRADEPRO_BAR_CACHE_S3_BUCKET.
        from .s3_mirror import mirror_from_env
        self._s3 = mirror_from_env(self.base_dir)

    # ── Public API ──────────────────────────────────────────────────

    def _chain_for(
        self, asset_class: str, resolution: str,
    ) -> tuple[list[str], str]:
        """Resolve the provider chain for the tuple. Returns
        ``(chain, source)`` where ``source`` is one of:
          * ``"preferences"`` — loader supplied a chain
          * ``"default"`` — fell back to the hardcoded default
        ``source`` is for the telemetry trail; callers don't branch on
        it. Phase B-3 introduces this; B-1/B-2 always returned the
        hardcoded default."""
        if self._preferences_loader is not None:
            try:
                chain = self._preferences_loader.chain_for(
                    asset_class, resolution,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "preferences chain_for(%s, %s) failed (default chain): %s",
                    asset_class, resolution, exc,
                )
                chain = None
            if chain:
                return chain, "preferences"
        return list(self._default_chain), "default"

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
        skip_fetch: bool = False,
    ) -> BarFrame:
        """Fetch bars for the (canonical, resolution, range) tuple.

        ``allow_partial`` is the OPT-IN flag for partial-data
        tolerance. Defaults to False — partial reads are the banned
        behaviour. A strategy that explicitly wants "give me what
        you can" sets it to True and gets back a frame plus the
        ``coverage_complete=False`` flag.

        ``force_refresh`` bypasses the cache check + re-pulls from
        the provider chain regardless of what's on disk. Used by
        the reload op.

        ``skip_fetch`` serves ONLY what is on disk — no provider is
        contacted even for missing partitions. Used by the harvest's
        circuit breaker once the provider chain is known-down for this
        run; combine with ``allow_partial=True``."""
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
        fetch_error: Optional[BarFetchError] = None

        t0 = time.perf_counter()

        for partition in partitions:
            partition_path = self._partition_path(
                canonical, asset_class, resolution, partition,
            )
            manifest_path = self._manifest_path(
                canonical, asset_class, resolution, partition,
            )

            need_fetch = force_refresh
            delta_from: _date_cls | None = None
            if self._ensure_local(manifest_path):  # read-through: shared S3 store
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
                    # Partition isn't fully complete for the month (e.g. it's
                    # a live month with future sessions uncached). Decide from
                    # the manifest which sessions the CALLER asked for are
                    # actually absent, and fetch ONLY those (delta), instead of
                    # re-pulling the whole month. The whole-month re-fetch was
                    # the harvest's dominant IBKR cost (5×7-day slices per
                    # partition per symbol per run) AND, combined with the
                    # replace-not-merge write path, produced the 21 Aug loop:
                    # rate-limited partial month → shrink refused → nothing
                    # written → next run re-pulls the whole month again.
                    if not force_refresh:
                        # Sessions must be judged against THIS partition's
                        # window, not the caller's full range — a request that
                        # straddles a month boundary otherwise marks a fully
                        # cached earlier month "incomplete" (its manifest can
                        # never contain the other month's sessions) and
                        # re-fetches it every run.
                        p_start, p_end = self._partition_range(plugin, partition)
                        # expected_session_dates is END-INCLUSIVE, but get()'s
                        # range is half-open [start, end) — without this filter
                        # the session sitting exactly on the end boundary
                        # (harvest passes end=tomorrow) reads as "missing"
                        # forever and the delta window degenerates.
                        req_end = min(end_utc, p_end)
                        requested = [
                            d for d in plugin.expected_session_dates(
                                max(start_utc, p_start), req_end,
                            )
                            if datetime(d.year, d.month, d.day,
                                        tzinfo=timezone.utc) < req_end
                        ]
                        actual_set = set(manifest.actual_session_dates)
                        missing = [
                            d for d in requested
                            if d.isoformat() not in actual_set
                        ]
                        cached_req = [
                            d for d in requested if d.isoformat() in actual_set
                        ]
                        # A session counts as "present" the moment ONE bar for
                        # it is on disk, so a live session harvested mid-day
                        # would freeze at its first write. Detect that from
                        # the manifest alone: if the total cached bar count is
                        # below what the cached sessions should hold, the tail
                        # session is short — re-fetch from it so intraday
                        # top-ups keep flowing and session tails heal.
                        short = False
                        if manifest.actual_session_dates:
                            expected_for_cached = sum(
                                plugin.expected_bar_count(
                                    resolution, _date_cls.fromisoformat(s),
                                )
                                for s in manifest.actual_session_dates
                            )
                            short = manifest.actual_bar_count < expected_for_cached
                        if not missing and not short:
                            chain_log.append("cache_hit_range")
                            continue
                        delta_dates = list(missing)
                        if short and cached_req:
                            delta_dates.append(max(cached_req))
                        if delta_dates:
                            delta_from = min(delta_dates)
                    need_fetch = True
                    chain_log.append("cache_incomplete")
            else:
                # No manifest = cache miss = must fetch
                need_fetch = True
                chain_log.append("cache_miss")

            if need_fetch and skip_fetch and not force_refresh:
                # Circuit-breaker mode: the caller already knows the chain is
                # down for this run — don't spend a doomed round-trip per
                # partition; the read below serves whatever is cached.
                chain_log.append("fetch_skipped")
                continue

            if need_fetch:
                # Fall through the provider chain for this partition.
                partition_start, partition_end = self._partition_range(
                    plugin, partition,
                )
                # Delta mode: ask the provider only for the window from the
                # first absent session forward, and MERGE the answer into the
                # cached partition instead of replacing it. Falls back to the
                # full partition window when there is nothing cached to
                # preserve (cache miss) or on force_refresh (re-source).
                fetch_window: tuple[datetime, datetime] | None = None
                _unsettled_only = False
                if delta_from is not None and not force_refresh:
                    _ds = max(
                        partition_start,
                        datetime(delta_from.year, delta_from.month,
                                 delta_from.day, tzinfo=timezone.utc),
                    )
                    _de = min(partition_end, end_utc)

                    # DON'T ASK FOR A BAR THAT CANNOT EXIST YET (27 Aug 2026).
                    #
                    # The daily harvest runs at 08:26 UTC — five hours before
                    # the US open. The only absent session was TODAY, so delta
                    # mode asked ibkr_web for [2026-08-27, 2026-08-27]. IBKR
                    # answered correctly ("21 bars returned, none within
                    # range"), the chain walk classified that correct answer as
                    # `ibkr_web_parse`, fell through, and yfinance wrote the
                    # whole partition. 237 of 244 symbols in one run, every
                    # morning, silently — and each one lays an adjusted-close
                    # row beside the raw IBKR history, widening the very seam
                    # ADJ_FACTOR_MIGRATION_PLAN exists to close.
                    #
                    # This is the same asymmetry `_sum_expected_bar_count`
                    # already fixed at the other end: that one stopped COUNTING
                    # sessions whose bars cannot be reached, while this one kept
                    # REQUESTING them. One half of the store knew today's bar
                    # does not exist yet; the other half asked for it anyway.
                    _last_settled = self._last_settled_session(
                        plugin, _ds, _de, now_utc=datetime.now(timezone.utc))
                    if _last_settled is not None:
                        _cap = min(_de, _last_settled)
                        if _cap <= _ds:
                            # Every session in the delta window is still open.
                            # There is nothing to fetch — serve cache and say
                            # so, rather than provoking a failure and a
                            # fallback write.
                            _unsettled_only = True
                            chain_log.append("delta_unsettled_skip")
                        else:
                            _de = _cap

                    if not _unsettled_only and _ds < _de:
                        fetch_window = (_ds, _de)
                        chain_log.append(
                            f"delta:{_ds.date().isoformat()}"
                            f"→{_de.date().isoformat()}"
                        )
                if _unsettled_only:
                    # Skip the chain walk entirely for this partition.
                    need_fetch = False
            if need_fetch:
                try:
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
                        fetch_window=fetch_window,
                        merge=fetch_window is not None,
                        allow_validated_shrink=force_refresh,
                    )
                except NoProviderAvailableError as exc:  # noqa: PERF203
                    # Don't let a provider outage make DATA ON DISK unreadable.
                    # 21 Aug 2026: with the IBKR session dark and Yahoo
                    # rate-limited, every read raised no_provider even though
                    # months of bars sat in the cache — the wheel board ran on
                    # 19-hour-old carried picks while the truth was local. An
                    # allow_partial caller gets whatever is cached (flagged
                    # coverage_complete=False); the outage is re-raised after
                    # the read only if the disk is ALSO empty, or the caller
                    # demanded completeness.
                    fetch_error = exc
                    chain_log.append("fetch_failed_serving_cache")
                    continue
                provider_used = chain_log[-1].split(":", 1)[0] \
                    if ":" in chain_log[-1] else chain_log[-1]

        # All partitions touched (cached or written). Now read the
        # parquet back + slice to the requested range.
        df, partitions_used = self._read_partitions(
            canonical, asset_class, resolution,
            partitions, start_utc, end_utc,
        )

        # A fetch failed somewhere above. Serve the cached rows only when the
        # caller opted into partial data AND the disk actually has something;
        # otherwise the outage stays loud.
        if fetch_error is not None and (df.empty or not allow_partial):
            raise fetch_error

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

        # Phase D-1: compute the data_state_hash from the manifests of
        # the partitions we actually touched. Reading manifests off
        # disk is cheap (small JSON), so we do it unconditionally —
        # the cost is amortised by the fetch itself when there was a
        # cache miss, and trivial on cache hits.
        data_state_hash = self._compute_data_state_hash(
            canonical=canonical, asset_class=asset_class,
            resolution=resolution, partitions=partitions_used,
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
            data_state_hash=data_state_hash,
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
        fetch_window: tuple[datetime, datetime] | None = None,
        merge: bool = False,
        allow_validated_shrink: bool = False,
    ) -> None:
        """Walk the provider chain until one succeeds. Write the
        parquet + manifest atomically. Caller catches the
        chain-exhausted case.

        ``fetch_window`` narrows the provider request to a sub-window of the
        partition (delta fetch); ``merge`` makes the write ADD to the cached
        partition instead of replacing it. The two travel together."""
        last_exc: Optional[BarFetchError] = None
        attempted: list[str] = []
        saw_empty = False

        chain, chain_source = self._chain_for(asset_class, resolution)
        # Leave breadcrumb in the audit trail so the cockpit can show
        # "this fetch used the operator-configured chain" vs "fell
        # back to default" — both lead the source_chain log when an
        # operator is debugging "why isn't my new provider being
        # tried?".
        chain_log.append(f"chain_source:{chain_source}")
        # Stash the resolved chain on the call so _write_partition
        # records it in the manifest.
        self._resolved_chain_for_call = chain

        for provider_name in chain:
            try:
                provider = get_provider(provider_name)
            except KeyError:
                chain_log.append(f"{provider_name}_unknown")
                continue
            if not provider.supports_resolution(resolution):
                chain_log.append(f"{provider_name}_unsupported")
                continue
            attempted.append(provider_name)

            # Clip the fetch window to the provider's max_history.
            # yfinance caps 1m at 7 days back; IBKR caps 1m at ~1 year.
            # If the partition window *starts* before the provider can
            # reach, clamp fetch_start forward so we request the
            # sub-window the provider can actually serve.  If the whole
            # window is out of range, skip this provider for this partition.
            max_hist = provider.max_history(resolution)
            fetch_start, fetch_end = fetch_window or (
                partition_start, partition_end,
            )
            if max_hist is not None:
                from datetime import timezone as _tz
                now_utc = datetime.now(_tz.utc).replace(
                    hour=0, minute=0, second=0, microsecond=0,
                )
                earliest_allowed = now_utc - max_hist
                if fetch_start < earliest_allowed:
                    fetch_start = earliest_allowed
                if fetch_start >= fetch_end:
                    chain_log.append(f"{provider_name}_out_of_range")
                    continue

            try:
                df, meta = provider.fetch(
                    canonical=canonical,
                    asset_class=asset_class,
                    resolution=resolution,
                    start=fetch_start,
                    end=fetch_end,
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
                # QUARANTINE, don't just vanish (22 Aug 2026): a rejected
                # frame used to leave no trace beyond a chain-log token, so a
                # provider serving garbage was an invisible problem. Preserve
                # the frame for inspection + say so in the central run log.
                try:
                    from pathlib import Path as _P
                    _qdir = _P.home() / ".tradepro" / "quarantine"
                    _qdir.mkdir(parents=True, exist_ok=True)

                    # REPORT A REPEAT REJECTION ONCE PER DAY, NOT ONCE PER RUN.
                    #
                    # The rejection itself is correct and must keep happening —
                    # a bad frame must never reach disk. What was wrong is the
                    # VOLUME: on 2026-08-25 this produced 356 warn-level run-log
                    # entries from just 25 distinct symbol-months, the same
                    # frames re-fetched and re-rejected up to 28 times each,
                    # because a permanently-bad upstream frame is retried on
                    # every harvest run.
                    #
                    # The cost is not disk (3.6MB) — it is that the owner opened
                    # the board and saw fourteen warnings about garbage being
                    # correctly refused, with ONE genuine degraded buried among
                    # them (IBKR market data dark, account-wide, mid-session).
                    # A guard that reports correct behaviour at the same volume
                    # and severity as a real fault trains people to stop reading
                    # the board, which costs more than the fault did.
                    #
                    # Not suppressed permanently: yfinance can and does fix its
                    # own history, so the frame is re-attempted and re-reported
                    # each day. Only the repetition within a day is collapsed.
                    _key = f"{canonical}_{partition}_{provider_name}"
                    _today = datetime.now(timezone.utc).strftime('%Y%m%d')
                    _already = any(f.name.startswith(f"reject_{_key}_{_today}")
                                   for f in _qdir.glob(f"reject_{_key}_*.parquet"))

                    _qname = (f"reject_{_key}_"
                              f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.parquet")
                    if not _already:
                        df.to_parquet(_qdir / _qname)
                        from ..run_log import log_run
                        log_run("bar-cache", "frame-quarantined", "warn",
                                broker=provider_name, symbol=canonical,
                                error=(f"{canonical} {partition}: {provider_name} frame "
                                       f"REJECTED by validation ({str(exc)[:120]}) — "
                                       f"preserved as quarantine/{_qname}"))
                    else:
                        _log.info(
                            "bar_cache: %s %s rejected again by %s (already reported "
                            "today; frame preserved from the first rejection)",
                            canonical, partition, provider_name)
                except Exception:  # noqa: BLE001 — quarantine must never break the chain walk
                    _log.debug("quarantine write failed (non-fatal)", exc_info=True)
                continue

            # Treat 0-row response as a soft provider failure.
            # Providers with range limits (e.g. yfinance: 7-day 1m cap)
            # return an empty DataFrame when asked for a wider partition
            # window.  Don't write an empty parquet over good cached data;
            # fall through to the next provider instead.
            if df.empty:
                saw_empty = True
                chain_log.append(f"{provider_name}_empty")
                continue

            # Atomic write. A shrink refusal (see _write_partition) is a
            # PROVIDER problem, not a fatal one: this provider's answer was
            # smaller than what we already hold, so treat it like any other
            # soft failure and let the next provider try. The cached data is
            # untouched either way.
            try:
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
                    merge=merge,
                    allow_validated_shrink=allow_validated_shrink,
                )
            except BarFetchError as exc:
                if exc.error_class != "partial_write_refused":
                    raise
                last_exc = exc
                chain_log.append(f"{provider_name}_partial_refused")
                continue
            chain_log.append(f"{provider_name}_ok")
            provider_versions[provider_name] = meta.get("provider_version", "")
            return

        # Chain exhausted. In delta (merge) mode an all-providers-empty
        # outcome is NOT a failure: it means "no new bars yet" — normal
        # pre-market, on holidays, and right after the close — and the cached
        # partition is intact. Raising here turned every quiet delta window
        # into a loud no_provider failure (the 21 Aug morning runs failed
        # every symbol this way before the session opened).
        # ANY provider affirmatively answering "zero bars in this recent
        # window" is a trustworthy no-new-bars signal (the chain is
        # golden-first), even if a later fallback errored.
        if merge and saw_empty:
            chain_log.append("delta_no_new_bars")
            chain_log.append("cache")   # provider_used → grade from disk
            return
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
        merge: bool = False,
        allow_validated_shrink: bool = False,
    ) -> None:
        """tmp + fsync + rename for the parquet, then for the manifest.
        Never leaves a partial file under the partition path."""
        partition_path.parent.mkdir(parents=True, exist_ok=True)

        # Filter dataframe to the partition window — provider might
        # have over-fetched slightly at the edges.
        if not df.empty:
            df = df[(df.index >= partition_start) & (df.index < partition_end)]

        # Delta merge: keep every cached row, add only timestamps we don't
        # already hold. Prefer-existing is deliberate — cached rows keep their
        # provenance (a golden ibkr_web row is never downgraded by a fallback
        # answer for the same timestamp); upgrading bronze→gold stays the job
        # of force_refresh (the resource-intraday lane). The merged frame is
        # always >= the cached one, so the shrink guard below can't fire on a
        # delta write.
        if merge and not df.empty and partition_path.exists():
            try:
                existing = pq.read_table(partition_path).to_pandas()
            except Exception:  # noqa: BLE001 — unreadable cache: replace it
                existing = pd.DataFrame()
            if not existing.empty:
                if "timestamp" in existing.columns:
                    existing = existing.set_index("timestamp")
                if existing.index.tz is None:
                    existing.index = existing.index.tz_localize("UTC")
                else:
                    existing.index = existing.index.tz_convert("UTC")
                fresh = df[~df.index.isin(existing.index)]
                df = pd.concat([existing, fresh], axis=0).sort_index()
                # …and then collapse to ONE ROW PER SESSION. `isin` above keys
                # on the exact instant, which is the right key intraday and the
                # WRONG one at 1d: providers disagree about what time stamps a
                # daily bar (ibkr_web 13:30 UTC, yfinance 04:00 UTC), so the
                # same session sails through as "new". See _dedupe_sessions.
                df, _ = _dedupe_sessions(
                    df, resolution, label=partition_path.name)

        # Safety net: never overwrite non-empty cached data with an empty
        # frame. _fetch_and_write should have caught this already (empty
        # result = soft failure → try next provider), but defence in depth.
        existing_rows = 0
        if partition_path.exists():
            try:
                _cached = pq.read_table(partition_path).to_pandas()
                if "timestamp" in _cached.columns:
                    _cached = _cached.set_index("timestamp")
                # Count SESSIONS, not rows. A partition already holding a
                # duplicated session would otherwise make the de-duplicating
                # write below look like a shrink, and the guard would refuse
                # it — leaving the corruption permanent, which is precisely
                # how the wrong-contract poison became immortal in August.
                existing_rows = len(_dedupe_sessions(_cached, resolution)[0])
            except Exception:  # noqa: BLE001
                try:
                    existing_rows = len(pq.read_table(partition_path))
                except Exception:  # noqa: BLE001
                    existing_rows = 0

        if df.empty and existing_rows > 0:
            _log.warning(
                "bar_cache: skipping empty write for %s — existing "
                "parquet has %d rows; keeping cached data",
                partition_path.name, existing_rows,
            )
            return

        # Never let a partition SHRINK silently. The empty-frame guard above
        # only catches the total-failure case; a provider that returns a
        # PARTIAL month (rate-limited, throttled, half-served) would sail past
        # it and replace a complete 22-session partition with a 5-bar stub,
        # because the write path is replace-not-merge. That is silent data
        # loss, and it is exactly what a bulk `force_refresh` re-harvest
        # invites. A shrink is a provider problem, so keep what we have and
        # say so LOUDLY — a smaller truth is still a loss.
        #
        # Equal-or-larger writes pass untouched: re-sourcing a month from a
        # better provider (yfinance → ibkr_web) is the whole point and keeps
        # the same session count.
        # POISON NEVER WINS ON SIZE (22 Aug 2026). The shrink guard counts
        # ROWS, so 4,900 wrong-contract bars beat 1,000 correct ones and the
        # poison became permanent — force_refresh could not dislodge STX's
        # 1m partition however many times it ran. A smaller TRUE partition is
        # worth more than a larger FALSE one, always.
        #
        # The incoming frame is the witness: the provider has just told us
        # what this instrument actually trades at. If the CACHED data sits an
        # order of magnitude away from that, the cached data is a different
        # listing (STX in LSE pence at 5,491 vs $1,013; MTUM at 5,495 vs
        # $324) and must be replaced regardless of size.
        if existing_rows > 0 and not df.empty and partition_path.exists():
            try:
                _old = pq.read_table(partition_path).to_pandas()
                if "close" in _old.columns and len(_old):
                    _old_max = float(pd.to_numeric(_old["close"], errors="coerce").max())
                    _new_max = float(pd.to_numeric(df["close"], errors="coerce").max())
                    if _new_max > 0 and _old_max > _new_max * 2.5:
                        _log.error(
                            "bar_cache: REPLACING %s — cached data tops out at "
                            "%.2f but %s reports %.2f for the same window; the "
                            "cached partition is a DIFFERENT LISTING, not more "
                            "data. Overwriting %d rows with %d.",
                            partition_path.name, _old_max, provider_used,
                            _new_max, existing_rows, len(df),
                        )
                        existing_rows = 0   # fall through to the normal write
            except Exception:  # noqa: BLE001 — never block a write on this check
                pass

        if existing_rows > 0 and 0 < len(df) < existing_rows:
            # VALIDATED-SHRINK exception (22 Aug 2026): "more rows = better"
            # is exactly wrong when the cached rows are phantoms. The
            # wrong-venue contracts (VLUE flat at 2536.93, STX in pence)
            # printed bars on days the US market was CLOSED, so the poisoned
            # months held MORE rows than the truth — and this guard kept the
            # poison immortal against every re-source. On an explicit
            # force_refresh, an incoming validated frame that covers EVERY
            # expected session of the partition (up to today) may replace a
            # larger cached one: rows beyond the session calendar are not
            # data, they are the corruption.
            if allow_validated_shrink:
                today = datetime.now(timezone.utc).date()
                # expected_session_dates is END-INCLUSIVE (same trap as the
                # delta-fetch boundary): asked for [1st, next-1st] it includes
                # the NEXT month's first session, which this month's frame can
                # never contain — clamp to the partition proper.
                expected = {
                    d for d in plugin.expected_session_dates(
                        partition_start, partition_end)
                    if d <= today and d < partition_end.date()
                }
                got = set(df.index.tz_convert("UTC").date)
                if expected and expected.issubset(got):
                    _log.warning(
                        "bar_cache: VALIDATED SHRINK of %s — replacing %d "
                        "cached rows with %d rows from %s covering every "
                        "expected session; the %d extra cached row(s) sat on "
                        "non-session dates (phantom bars from a wrong "
                        "contract).",
                        partition_path.name, existing_rows, len(df),
                        provider_used, existing_rows - len(df),
                    )
                    existing_rows = 0   # fall through to the normal write
        if existing_rows > 0 and 0 < len(df) < existing_rows:
            _log.error(
                "bar_cache: REFUSING to shrink %s — provider %s returned %d "
                "rows over the partition window but %d are already cached. "
                "Keeping the cached data; this partition was NOT refreshed.",
                partition_path.name, provider_used, len(df), existing_rows,
            )
            raise BarFetchError(
                error_class="partial_write_refused",
                provider=provider_used,
                canonical=canonical,
                message=(
                    f"{provider_used} returned {len(df)} rows for "
                    f"{canonical} {partition}, fewer than the {existing_rows} "
                    f"already cached — refusing to overwrite good data with a "
                    f"partial fetch"
                ),
                retry_strategy="retry_later",
            )

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
        # The chain we ACTUALLY tried — may be from preferences
        # (operator-configured) or the hardcoded default. Recording
        # the resolved chain (not ``self._default_chain``) keeps the
        # manifest honest about provenance.
        resolved_chain = getattr(
            self, "_resolved_chain_for_call", self._default_chain,
        )
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
            provider_chain=list(resolved_chain),
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

        # Write-through to the SHARED S3 store so every env reads this harvest
        # (no per-env re-harvest). Best-effort: an S3 failure keeps the local
        # copy and logs — the harvest still succeeds.
        if self._s3 is not None:
            self._s3.upload(partition_path)
            self._s3.upload(manifest_path)

    def _ensure_local(self, path: Path) -> bool:
        """True if `path` is available locally — fetching it from the shared S3
        store on a local miss (read-through). Falls back to local-only when S3
        is disabled/unreachable, so an env with no S3 still works."""
        if path.exists():
            return True
        if self._s3 is not None and self._s3.download(path):
            return True
        return path.exists()

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
            if not self._ensure_local(path):  # read-through: shared S3 store
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
    def _last_settled_session(
        plugin: AssetClassPlugin,
        start_utc: datetime,
        end_utc: datetime,
        now_utc: datetime,
    ) -> datetime | None:
        """End of the last session in [start, end) whose bars are final.

        Returns the instant just after that session's close, so a caller can
        use it as an exclusive upper bound. None means "cannot tell" — either
        the plugin does not model a close (24h venues) or there are no sessions
        in range — and the caller must then leave the window alone rather than
        guess.

        The settled test is the plugin's `session_close_utc`, NOT a hardcoded
        20:00. 16:00 ET is 20:00 UTC in summer and 21:00 UTC in winter, and
        half-days close at 13:00 ET — all of which the plugin already knows
        because it needs them for `expected_bar_count`.
        """
        # REACHABILITY, the same rule `_sum_expected_bar_count` applies at the
        # other end. `expected_session_dates` is inclusive of the end DATE
        # while fetch windows are half-open, so without this filter a session
        # sitting exactly on the exclusive bound would be considered — the
        # precise off-by-one that produced phantom sessions in the expected
        # count. One rule, both halves.
        sessions = [
            d for d in plugin.expected_session_dates(start_utc, end_utc)
            if datetime(d.year, d.month, d.day, tzinfo=timezone.utc) < end_utc
        ]
        # Return the bound as MIDNIGHT AFTER the last settled session, not the
        # close instant. The caller uses it as an exclusive upper bound against
        # bar timestamps, and a provider may stamp a daily bar AT the close —
        # capping at 20:00 would then drop the very bar the session settled.
        # Midnight-after excludes unsettled days without truncating settled
        # ones, and keeps the bound on the same day boundary the rest of the
        # windowing uses.
        settled: datetime | None = None
        for d in sessions:
            close = plugin.session_close_utc(d)
            if close is None:
                return None          # class does not model a close — don't clamp
            if close <= now_utc:
                settled = datetime(d.year, d.month, d.day,
                                   tzinfo=timezone.utc) + timedelta(days=1)
        if settled is None:
            # Sessions exist but none has closed yet. Signal that by returning
            # the start of the window: every candidate session is unsettled.
            return start_utc if sessions else None
        return settled

    @staticmethod
    def _sum_expected_bar_count(
        plugin: AssetClassPlugin,
        resolution: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> int:
        """Bars we EXPECT to see, counted over the same interval the rows are
        filtered on.

        The denominator and the numerator disagreed. Rows are selected with
        ``index < end_utc`` (half-open, see _slice_range), but
        ``expected_session_dates`` is inclusive of the end DATE. So whenever
        ``end_utc`` landed on midnight of a trading day, every one of that
        day's bars was excluded from the numerator while the day was still
        counted in full in the denominator — a phantom session's worth of bars
        that can never be filled.

        Measured on AAPL 5m, Mon 2026-08-17 → Fri 2026-08-21: returned 312,
        expected 390. The 78-bar shortfall is exactly Friday, whose bars all
        sit at or after Friday midnight and are therefore filtered out.

        That shortfall is not cosmetic: `coverage_complete` is
        `rows_returned >= rows_expected`, so it flips to False, which demotes
        the harvest's quality tier from GOLD to SILVER and feeds the data
        screen. A grade that moves with the request boundary rather than with
        the data teaches people to ignore the grade — the cry-wolf failure this
        module has already had to walk back once (see the note on `_tier` in
        cli/bar_cache_harvest.py, which had every symbol BRONZE while the disk
        was overwhelmingly IBKR-gold).

        A session contributes bars only if any of its bars can satisfy
        ``index < end_utc``. Every bar on date ``d`` is at or after midnight on
        ``d``, so if that midnight is already >= end_utc the whole session is
        unreachable and must not be counted.
        """
        sessions = plugin.expected_session_dates(start_utc, end_utc)
        reachable = [
            d for d in sessions
            if datetime(d.year, d.month, d.day, tzinfo=timezone.utc) < end_utc
        ]
        return sum(plugin.expected_bar_count(resolution, d) for d in reachable)

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
            # No partitions read → sentinel hash so a downstream
            # backtest can pivot on "no data state" cleanly.
            data_state_hash=EMPTY_DATA_STATE_HASH,
        )

    def _compute_data_state_hash(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        partitions: list[str],
    ) -> str:
        """Build the per-partition fingerprints from disk + hash.

        Reads each touched partition's manifest. A missing manifest
        is treated as "absent fingerprint" — the partition is dropped
        from the hash rather than the call failing. The manifest
        validation in ``get()`` would have raised earlier if the
        manifest were unreadable, so this is just defence in depth
        against a partition that exists in the partition list but
        had its manifest deleted out-of-band between fetch + hash.
        """
        if not partitions:
            return EMPTY_DATA_STATE_HASH
        fingerprints: list[PartitionFingerprint] = []
        for partition in partitions:
            manifest_path = self._manifest_path(
                canonical, asset_class, resolution, partition,
            )
            if not self._ensure_local(manifest_path):  # read-through: shared S3
                continue
            try:
                manifest = Manifest.read(manifest_path)
            except Exception:  # noqa: BLE001
                continue
            fingerprints.append(fingerprint_from_manifest(manifest))
        return compute_data_state_hash(fingerprints)

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


def _is_daily_or_coarser(resolution: str) -> bool:
    """True for 1d and anything longer. Intraday resolutions are excluded
    because two bars an hour apart are two genuine bars there."""
    r = (resolution or "").strip().lower()
    return r.endswith(("d", "day", "w", "wk", "week", "mo", "month", "y", "year"))


def _dedupe_sessions(
    df: pd.DataFrame, resolution: str, *, label: str = "",
) -> tuple[pd.DataFrame, int]:
    """One SESSION, one row — for daily-and-coarser data only.

    THE BUG THIS FIXES (25 Aug 2026, 103 symbols corrupted in one night).
    The delta merge de-duplicated on the exact TIMESTAMP:

        fresh = df[~df.index.isin(existing.index)]

    and providers do not agree on what instant stamps a daily bar. ibkr_web
    stamps the US cash open, 13:30 UTC. yfinance stamps 04:00 UTC. So the SAME
    session arrives with two different instants, `isin` correctly reports it as
    new, and the partition ends up holding 2026-08-24 twice with two different
    closes (TXN: 256.59 from ibkr_web, 258.94 from yfinance).

    The comment above the merge promised that "a golden ibkr_web row is never
    downgraded by a fallback answer for the same timestamp". The promise was
    real; the KEY was wrong, so at 1d it silently did not hold.

    What it cost: every 20-day mean and standard deviation computed over an
    affected window had 21 bars in it, one of them a phantom with a different
    close. That is not a rounding difference — it moved TXN from 2.53σ to
    under the 2.5σ trigger, so the Swing screen published a candidate at 00:15
    and withdrew it at 02:17 having been given no new information.

    Resolution: keep ONE row per UTC calendar date, preferring a golden
    (IBKR) source over a fallback, and preferring the row already cached when
    provenance ties — which is the prefer-existing policy the merge always
    intended. Dropping is logged at WARNING, never silently.
    """
    if df.empty or not _is_daily_or_coarser(resolution):
        return df, 0
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    else:
        idx = idx.tz_convert("UTC")
    key = idx.normalize()
    if not key.has_duplicates:
        return df, 0

    if "source" in df.columns:
        fallback = ~df["source"].astype(str).isin(list(_GOLDEN_BAR_SOURCES))
    else:
        fallback = pd.Series(True, index=df.index)
    # VOLUME BREAKS THE TIE BEFORE ARRIVAL ORDER DOES.
    #
    # Same session, same provider is a tie on provenance, and prefer-existing
    # then keeps whichever row was cached FIRST. During a live session that is
    # the PARTIAL bar: any 1d fetch made before 20:00 UTC captures the day
    # so far, and the settled bar fetched after the close could never replace
    # it. Measured 2026-08-26 on the 25 Aug session, 10 of 10 symbols wrong:
    #
    #     NVDA  stored 211.05 vs true 213.05  (0.94%)   volume 55% of true
    #     MSFT  stored 488.70 vs true 491.71  (0.61%)   volume 45%
    #     SPY   stored 765.09 vs true 765.91  (0.11%)   volume 39%
    #
    # A partial close is not a rounding difference: 0.95% is the whole gap
    # between a 2.53-sigma Swing signal and a 2.32-sigma one, so the screen was
    # computing entries on a price the market never closed at.
    #
    # The discriminator needs no fetch-time metadata, which is why it is this
    # one: for the same session, the more complete bar has MORE VOLUME. A
    # partial day cannot have traded more than the full day it is part of.
    # Prefer-existing still applies when volume ties, so a genuine re-fetch of
    # identical data changes nothing.
    if "volume" in df.columns:
        _vol = pd.to_numeric(df["volume"], errors="coerce").fillna(-1)
        _v = (-_vol).to_numpy()               # higher volume sorts first
    else:
        _v = pd.Series(0, index=df.index).to_numpy()

    tmp = df.assign(
        _k=key,
        _g=fallback.to_numpy().astype(int),   # 0 = golden, sorts first
        _v=_v,                                # more volume = more complete
        _p=range(len(df)),                    # 0 = already cached, sorts first
    )
    kept = (tmp.sort_values(["_k", "_g", "_v", "_p"], kind="stable")
               .drop_duplicates("_k", keep="first")
               .sort_values("_p", kind="stable")
               .drop(columns=["_k", "_g", "_v", "_p"]))
    dropped = len(df) - len(kept)
    if dropped:
        dupe_days = sorted({str(d.date()) for d in key[key.duplicated(keep=False)]})
        _log.warning(
            "bar_cache: %s held %d row(s) for %d already-present session(s) "
            "%s — keeping the golden-source row per session and dropping the "
            "rest. Two providers stamp a daily bar at different instants; "
            "this is that, not new data.",
            label or "partition", dropped, len(dupe_days),
            ", ".join(dupe_days[:6]) + ("…" if len(dupe_days) > 6 else ""),
        )
    return kept.sort_index(), dropped


def _ensure_utc(ts: datetime) -> datetime:
    """Normalise a datetime to tz-aware UTC. Naive input is assumed
    UTC (consistent with how the rest of the project handles
    timestamps — UTC everywhere)."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)
