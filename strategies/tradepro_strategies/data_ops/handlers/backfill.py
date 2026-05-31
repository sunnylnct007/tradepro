"""``data_backfill`` handler — operator-triggered cache population.

Phase C-Backfill slice of the trustworthy-data roadmap. This is the
first **actually useful operator action**: an operator clicks
"Backfill missing" on the cockpit, the worker pulls multi-year
history through the BarStore + provider chain, and the per-symbol
coverage panel updates to show the new state.

Non-destructive in the sense that backfill is *additive* — it pulls
bars the cache doesn't have, then writes new partitions or fills
gaps in existing ones via the BarStore's atomic-write path. The
existing partitions are not overwritten; that's ``data_reload``'s
job (a future handler that explicitly forces re-fetch).

Multi-service note (per ROADMAP principle #9): the handler reads
through ``BarCacheStorage`` only to introspect the symbol's pre-
existing state (so it can report "we had N partitions before, M
after"). The actual fetch goes through the BarStore — whose
provider chain reaches the .NET API, which talks to IG / yfinance.
A future S3-backed deployment swaps the storage class; the BarStore's
network calls don't care where the parquet bytes ultimately live.
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


_log = logging.getLogger("tradepro.data_ops.backfill")


@register_data_op("data_backfill")
class BackfillHandler(DataOpHandler):
    """Pulls bars for (canonical, asset_class, resolution, from, to)
    through the BarStore + configured provider chain.

    Required params:
      ``canonical``         — symbol key (e.g. "SPY")
      ``asset_class``       — plugin name (e.g. "us_etf")
      ``resolution``        — "1m" / "5m" / "1h" / "1d" / ...
      ``from``              — YYYY-MM-DD inclusive
      ``to``                — YYYY-MM-DD inclusive (today if omitted)

    Optional params:
      ``allow_partial``     — default True. The handler always asks
                              the BarStore for partial reads because
                              the operator's intent is "get me what
                              you can"; coverage_complete is reported
                              honestly in the result. Set to False
                              to fail-loud if any gap remains.
      ``api_base``          — backend URL for BarStore's IGProvider
                              proxy + PreferencesLoader. Defaults to
                              ``TRADEPRO_API_URL`` env or
                              ``http://localhost:5252``.

    The handler returns:
      ``ok=True``  on a successful fetch (even if coverage is partial
                   — ok signals "the handler did its job", not "the
                   data is complete"; consumers look at
                   detail.coverage_complete for the data fact).
      ``ok=False`` on missing required params, unparseable dates,
                   storage-write failures, or every-provider-failed
                   scenarios (the BarStore raises ``NoProviderAvailableError``).
    """

    def handle(
        self, request: DataOpRequest, storage: BarCacheStorage,
    ) -> DataOpResult:
        # Late imports — avoid loading bar_cache + http stacks for
        # the registry / list-kinds path. Same convention the rest
        # of data_ops uses.
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
        allow_partial_raw = params.get("allow_partial", True)
        api_base = str(params.get("api_base") or "").strip() or None

        missing = [
            k for k, v in (
                ("canonical", canonical),
                ("asset_class", asset_class),
                ("resolution", resolution),
                ("from", from_str),
            ) if not v
        ]
        if missing:
            return DataOpResult(
                ok=False,
                summary=f"missing required params: {missing}",
                error="missing required params",
                detail={
                    "received": dict(params),
                    "required": ["canonical", "asset_class", "resolution", "from"],
                    "missing": missing,
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
                detail={"from": from_str, "to": to_str},
            )

        if end_dt < start_dt:
            return DataOpResult(
                ok=False,
                summary="to date must be on or after from date",
                error=f"to={to_str!r} < from={from_str!r}",
            )

        allow_partial = _truthy(allow_partial_raw)

        # ── BarStore wiring ────────────────────────────────────────
        # Resolve the base directory from the storage handle when
        # possible — LocalBarCacheStorage exposes base_dir; remote
        # backends will provide their own resolver in Phase I.
        base_dir = _resolve_base_dir(storage)
        if base_dir is None:
            return DataOpResult(
                ok=False,
                summary="storage backend doesn't expose a base_dir suitable for the BarStore",
                error="non-local storage not yet supported by BackfillHandler",
                detail={"storage": storage.describe()},
            )

        # Snapshot pre-state so the operator-facing result can show
        # "X partitions before, Y after" — answers "did anything
        # change?" without a separate validate call.
        pre_partition_count = _count_partitions(
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

        store = BarStore(
            base_dir=base_dir,
            telemetry=telemetry,
            preferences_loader=preferences_loader,
        )

        try:
            frame = store.get(
                canonical=canonical,
                asset_class=asset_class,
                resolution=resolution,
                start=start_dt,
                end=end_dt,
                allow_partial=allow_partial,
                fetched_by="data_backfill",
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
                    "partitions_before": pre_partition_count,
                },
            )
        except Exception as exc:  # noqa: BLE001
            return DataOpResult(
                ok=False,
                summary=f"BarStore raised unexpectedly: {type(exc).__name__}",
                error=f"{type(exc).__name__}: {exc}",
            )

        post_partition_count = _count_partitions(
            storage, asset_class, canonical, resolution,
        )
        partitions_added = max(
            0, post_partition_count - pre_partition_count,
        )

        return DataOpResult(
            ok=True,
            summary=(
                f"{frame.rows_returned} of {frame.rows_expected} bars "
                f"({'COMPLETE' if frame.coverage_complete else 'PARTIAL'}) "
                f"via {frame.provider_used} "
                f"[+{partitions_added} partitions]"
            ),
            detail={
                "canonical": canonical,
                "asset_class": asset_class,
                "resolution": resolution,
                "from": from_str,
                "to": to_str or end_dt.date().isoformat(),
                "rows_returned": frame.rows_returned,
                "rows_expected": frame.rows_expected,
                "coverage_complete": frame.coverage_complete,
                "partitions_used": frame.partitions_used,
                "provider_chain_tried": frame.provider_chain_tried,
                "provider_used": frame.provider_used,
                "schema_version": frame.schema_version,
                "fetched_at_utc": frame.fetched_at_utc,
                "partitions_before": pre_partition_count,
                "partitions_after": post_partition_count,
                "partitions_added": partitions_added,
                "storage": storage.describe(),
            },
        )


# ─── Helpers ─────────────────────────────────────────────────────


def _parse_date(s: str) -> datetime:
    """YYYY-MM-DD → tz-aware UTC midnight. Accepts "today" as a
    convenience for the operator-supplied `to` field."""
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
    """LocalBarCacheStorage carries ``base_dir`` directly. A future
    S3-backed implementation will need its own bridge (probably a
    local sync + delayed upload pattern); for Phase C-Backfill we
    explicitly require LocalBarCacheStorage so the contract is
    visible in the error if a future deployment swaps it in."""
    if isinstance(storage, LocalBarCacheStorage):
        return storage.base_dir
    return None


def _count_partitions(
    storage: BarCacheStorage,
    asset_class: str,
    canonical: str,
    resolution: str,
) -> int:
    """Total number of partitions present for the tuple. Used for
    the ``partitions_before / partitions_after`` audit fields."""
    count = 0
    if not storage.symbol_exists(asset_class, canonical):
        return 0
    if resolution not in storage.list_resolutions(asset_class, canonical):
        return 0
    for _ in storage.list_manifests(asset_class, canonical, resolution):
        count += 1
    return count
