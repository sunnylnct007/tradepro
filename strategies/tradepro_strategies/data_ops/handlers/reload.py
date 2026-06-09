"""``data_reload`` handler — destructive force-refresh of a partition range.

Phase C-Reload of the trustworthy-data roadmap. The first **destructive**
data op: overwrites existing partitions for a (canonical, asset_class,
resolution) tuple over a date range. Used when:

  * A corp action (split, dividend, ticker rename) drifted the
    yfinance-adjusted prices and the cache holds the pre-action data.
  * A provider revision is known-bad and we want to re-fetch from the
    chain's current state (e.g. yfinance silently revised historicals).
  * The schema changed and the manifest needs a re-write (this is
    cleaner via ``data_repartition`` once it ships, but for now Reload
    works as a heavy hammer).

Safety affordances (matching Phase A's UI-trigger design contract for
destructive ops):

  * ``reason`` field is REQUIRED in params. Without it the handler
    refuses to run. The reason flows into the audit trail and the
    cockpit's session detail view so a future investigator can answer
    "who forced a reload on SPY at 14:00, and why?".
  * The handler reports ``partitions_overwritten`` in the result detail
    so the cockpit shows exactly what changed. Diffs the partition's
    manifest ``fetched_at_utc`` timestamps before / after to count how
    many were actually replaced.

Internals match BackfillHandler's shape — same param schema plus
``reason``, same BarStore wiring, same multi-service contract via
``LocalBarCacheStorage``. The only behavioural difference is
``force_refresh=True`` on the BarStore.get() call, which makes the
store skip the cache-hit / partial check and re-pull every partition
in the range from the configured provider chain.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..registry import DataOpHandler, register_data_op
from ..storage import BarCacheStorage
from ..storage.local import LocalBarCacheStorage
from ..types import DataOpRequest, DataOpResult


_log = logging.getLogger("tradepro.data_ops.reload")


@register_data_op("data_reload")
class ReloadHandler(DataOpHandler):
    """Force re-fetch + overwrite partitions for a (canonical, asset_class,
    resolution) tuple over a date range.

    Required params:
      ``canonical``    — symbol key (e.g. "SPY")
      ``asset_class``  — plugin name (e.g. "us_etf")
      ``resolution``   — "1m" / "5m" / "1h" / "1d" / ...
      ``from``         — YYYY-MM-DD inclusive
      ``reason``       — non-empty text. Operator must declare WHY.

    Optional:
      ``to``           — YYYY-MM-DD (today if omitted)
      ``allow_partial`` — default True
      ``api_base``     — backend URL for telemetry + preferences. Defaults
                          to ``TRADEPRO_API_URL`` env.

    Distinguished from BackfillHandler by:
      * ``reason`` is mandatory (declares intent for the audit trail)
      * ``force_refresh=True`` on BarStore.get (re-pulls every partition
        regardless of whether the cache thinks it's complete)
      * Result reports ``partitions_overwritten`` rather than just
        ``partitions_added`` — diffs pre vs post manifest timestamps
        to count which partitions were actually replaced
    """

    def handle(
        self, request: DataOpRequest, storage: BarCacheStorage,
    ) -> DataOpResult:
        try:
            from tradepro_strategies.bar_cache import BarFetchError, BarStore
            from tradepro_strategies.bar_cache.asset_classes import (  # noqa: F401
                UsEtfPlugin,
            )
            from tradepro_strategies.bar_cache.providers import (  # noqa: F401
                YFinanceProvider,
            )
            from tradepro_strategies.bar_cache.preferences import (
                PreferencesLoader,
            )
            from tradepro_strategies.bar_cache.telemetry import (
                BackendTelemetrySink,
                TelemetrySink,
            )
        except Exception as exc:  # noqa: BLE001
            return DataOpResult(
                ok=False,
                summary="bar_cache stack unavailable",
                error=f"import failed: {exc}",
            )

        params = request.params or {}

        canonical = str(params.get("canonical") or "").strip()
        asset_class = str(params.get("asset_class") or "").strip()
        resolution = str(params.get("resolution") or "").strip()
        from_str = str(params.get("from") or "").strip()
        to_str = str(params.get("to") or "").strip()
        reason = str(params.get("reason") or "").strip()
        allow_partial_raw = params.get("allow_partial", True)
        api_base = str(params.get("api_base") or "").strip() or None
        # ibkr_only: IBKR as sole provider — no yfinance fallback.
        # For a Reload (force_refresh=True) you almost always want
        # IBKR-quality data replacing what was there; otherwise you
        # risk overwriting good IBKR partitions with yfinance stubs.
        # Default True for reload (unlike backfill where it defaults False).
        ibkr_only = _truthy(params.get("ibkr_only", True))

        # Reason is the safety rail for the destructive op. We require
        # non-empty text — not a checkbox, not "yes" — so the audit
        # log carries a real explanation. The cockpit's modal enforces
        # this client-side too; this is the defence-in-depth check.
        missing = [
            k for k, v in (
                ("canonical", canonical),
                ("asset_class", asset_class),
                ("resolution", resolution),
                ("from", from_str),
                ("reason", reason),
            ) if not v
        ]
        if missing:
            return DataOpResult(
                ok=False,
                summary=f"missing required params: {missing}",
                error="missing required params",
                detail={
                    "received": dict(params),
                    "required": [
                        "canonical", "asset_class", "resolution",
                        "from", "reason",
                    ],
                    "missing": missing,
                    "note": (
                        "reason is mandatory for destructive ops. "
                        "Audit trail keeps it visible to investigators."
                    ),
                },
            )

        try:
            start_dt = _parse_date(from_str)
            end_dt = _parse_date(to_str) if to_str else _today_utc()
        except ValueError as exc:
            return DataOpResult(
                ok=False,
                summary=f"date parse error: {exc}",
                error=str(exc),
                detail={"from": from_str, "to": to_str, "reason": reason},
            )

        if end_dt < start_dt:
            return DataOpResult(
                ok=False,
                summary="to date must be on or after from date",
                error=f"to={to_str!r} < from={from_str!r}",
            )

        allow_partial = _truthy(allow_partial_raw)

        base_dir = _resolve_base_dir(storage)
        if base_dir is None:
            return DataOpResult(
                ok=False,
                summary="storage backend doesn't expose a base_dir suitable for the BarStore",
                error="non-local storage not yet supported by ReloadHandler",
                detail={"storage": storage.describe()},
            )

        # Snapshot pre-state — record every existing manifest's
        # fetched_at_utc so post-fetch we can count which ones were
        # actually replaced. Manifests that get overwritten will have
        # a strictly-greater fetched_at_utc after the BarStore call.
        pre_state = _snapshot_manifest_timestamps(
            storage, asset_class, canonical, resolution,
        )

        telemetry: TelemetrySink
        preferences_loader: Optional[PreferencesLoader]
        if api_base:
            telemetry = BackendTelemetrySink(base_dir=base_dir, api_base=api_base)
            preferences_loader = PreferencesLoader(api_base=api_base)
        else:
            telemetry = TelemetrySink(base_dir=base_dir)
            preferences_loader = None

        # Provider chain: reload defaults to ibkr_only=True so we
        # always replace with high-quality data and never overwrite
        # good IBKR partitions with yfinance stubs.
        provider_chain = ["ibkr"] if ibkr_only else None  # None → use preferences

        store = BarStore(
            base_dir=base_dir,
            telemetry=telemetry,
            preferences_loader=preferences_loader,
            provider_chain=provider_chain,
        )

        try:
            frame = store.get(
                canonical=canonical,
                asset_class=asset_class,
                resolution=resolution,
                start=start_dt,
                end=end_dt,
                allow_partial=allow_partial,
                # The key difference from backfill: ignore the cache
                # and re-pull every partition in the range.
                force_refresh=True,
                fetched_by=f"data_reload:{reason[:60]}",
            )
        except BarFetchError as exc:
            return DataOpResult(
                ok=False,
                summary=(
                    f"BarStore failed: {exc.error_class} ({exc.retry_strategy})"
                ),
                error=str(exc),
                detail={
                    "error_class": exc.error_class,
                    "retry_strategy": exc.retry_strategy,
                    "expected": exc.expected,
                    "actual": exc.actual,
                    "canonical": canonical,
                    "asset_class": asset_class,
                    "resolution": resolution,
                    "from": from_str,
                    "to": to_str or end_dt.date().isoformat(),
                    "reason": reason,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return DataOpResult(
                ok=False,
                summary=f"BarStore raised unexpectedly: {type(exc).__name__}",
                error=f"{type(exc).__name__}: {exc}",
            )

        post_state = _snapshot_manifest_timestamps(
            storage, asset_class, canonical, resolution,
        )
        overwritten, added = _diff_manifest_timestamps(pre_state, post_state)

        return DataOpResult(
            ok=True,
            summary=(
                f"{frame.rows_returned} of {frame.rows_expected} bars "
                f"({'COMPLETE' if frame.coverage_complete else 'PARTIAL'}) "
                f"via {frame.provider_used} "
                f"[overwrote {overwritten}, added {added}]"
            ),
            detail={
                "canonical": canonical,
                "asset_class": asset_class,
                "resolution": resolution,
                "from": from_str,
                "to": to_str or end_dt.date().isoformat(),
                "reason": reason,
                "rows_returned": frame.rows_returned,
                "rows_expected": frame.rows_expected,
                "coverage_complete": frame.coverage_complete,
                "partitions_used": frame.partitions_used,
                "provider_chain_tried": frame.provider_chain_tried,
                "provider_used": frame.provider_used,
                "schema_version": frame.schema_version,
                "fetched_at_utc": frame.fetched_at_utc,
                "partitions_overwritten": overwritten,
                "partitions_added": added,
                "storage": storage.describe(),
            },
        )


# ─── Helpers ─────────────────────────────────────────────────────


def _parse_date(s: str) -> datetime:
    s = s.strip()
    if s.lower() == "today":
        return _today_utc()
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(
            f"expected YYYY-MM-DD or 'today', got {s!r}: {exc}"
        )


def _today_utc() -> datetime:
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes", "y"}
    return bool(v)


def _resolve_base_dir(storage: BarCacheStorage) -> Optional[Path]:
    if isinstance(storage, LocalBarCacheStorage):
        return storage.base_dir
    return None


def _snapshot_manifest_timestamps(
    storage: BarCacheStorage,
    asset_class: str,
    canonical: str,
    resolution: str,
) -> dict[str, str]:
    """Map partition → fetched_at_utc for every existing manifest.
    Empty dict when the symbol / resolution has no partitions yet."""
    out: dict[str, str] = {}
    if not storage.symbol_exists(asset_class, canonical):
        return out
    if resolution not in storage.list_resolutions(asset_class, canonical):
        return out
    # list_manifests yields (partition_key, manifest) tuples — the
    # storage interface keeps the partition name separate from the
    # manifest payload so a future S3 backend can derive partition
    # from the S3 key rather than re-parsing the manifest body.
    for partition, manifest in storage.list_manifests(asset_class, canonical, resolution):
        # ``manifest`` may be a sentinel "unreadable" object (see
        # storage.local._UnreadableManifest) — handle by skipping,
        # which is fine for the overwrite diff (an unreadable
        # manifest doesn't count as a "before" state we can compare).
        ts = getattr(manifest, "fetched_at_utc", None)
        if ts:
            out[partition] = ts
    return out


def _diff_manifest_timestamps(
    pre: dict[str, str],
    post: dict[str, str],
) -> tuple[int, int]:
    """Returns (overwritten_count, added_count).

    A partition was overwritten when its fetched_at_utc strictly
    increased between pre and post snapshots. Added means a partition
    that exists in post but not in pre. We don't count partitions that
    appear in pre but not in post (impossible during a Reload — the
    handler doesn't delete) so the math is just two simple comparisons.
    """
    overwritten = 0
    added = 0
    for partition, post_ts in post.items():
        pre_ts = pre.get(partition)
        if pre_ts is None:
            added += 1
        elif post_ts > pre_ts:
            overwritten += 1
    return overwritten, added
