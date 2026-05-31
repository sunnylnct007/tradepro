"""``data_validate`` handler — walks the cache for one symbol and
reports per-resolution gaps.

Non-destructive: only reads manifests via the storage backend. Safe
to run at any time, against any storage backend (local or future S3).

The output ``DataOpResult.detail`` is rendered by the cockpit's
session-detail drill-in. ``summary`` is the one-line cockpit list
display.
"""
from __future__ import annotations

import logging
from typing import Any

from ..registry import DataOpHandler, register_data_op
from ..storage import BarCacheStorage
from ..storage.local import _UnreadableManifest
from ..types import DataOpRequest, DataOpResult


_log = logging.getLogger("tradepro.data_ops.validate")


@register_data_op("data_validate")
class ValidateHandler(DataOpHandler):
    """Validates a (canonical, asset_class) tuple by reading every
    on-disk manifest and counting complete vs incomplete partitions
    per resolution.

    Required params: ``canonical``, ``asset_class``.
    Optional params: none today; future-proofed for ``resolution``
    so the operator can validate a single resolution rather than all.
    """

    def handle(
        self, request: DataOpRequest, storage: BarCacheStorage,
    ) -> DataOpResult:
        canonical = str(request.params.get("canonical") or "").strip()
        asset_class = str(request.params.get("asset_class") or "").strip()
        if not canonical or not asset_class:
            return DataOpResult(
                ok=False,
                summary="canonical + asset_class required",
                error="missing required params",
                detail={
                    "received": dict(request.params),
                    "required": ["canonical", "asset_class"],
                },
            )

        if not storage.symbol_exists(asset_class, canonical):
            return DataOpResult(
                ok=True,    # operator-visible "no cache" isn't a failure
                summary="no cache directory for this symbol",
                detail={
                    "canonical": canonical,
                    "asset_class": asset_class,
                    "exists": False,
                    "storage": storage.describe(),
                },
            )

        resolutions: dict[str, dict[str, Any]] = {}
        for resolution in storage.list_resolutions(asset_class, canonical):
            partitions: list[dict[str, Any]] = []
            for partition_id, manifest in storage.list_manifests(
                asset_class, canonical, resolution,
            ):
                if isinstance(manifest, _UnreadableManifest):
                    partitions.append({
                        "partition": partition_id,
                        "error": f"manifest unreadable: {manifest.exc}",
                    })
                    continue
                partitions.append({
                    "partition": partition_id,
                    "expected_bar_count": manifest.expected_bar_count,
                    "actual_bar_count": manifest.actual_bar_count,
                    "missing_sessions": manifest.missing_session_dates(),
                    "is_complete": manifest.is_complete(),
                    "schema_version": manifest.schema_version,
                    "provider_used": manifest.provider_used,
                    "file_size_bytes": manifest.file_size_bytes,
                    "fetched_at_utc": manifest.fetched_at_utc,
                })
            incomplete = sum(
                1 for p in partitions if not p.get("is_complete")
            )
            resolutions[resolution] = {
                "partition_count": len(partitions),
                "complete_count": len(partitions) - incomplete,
                "incomplete_count": incomplete,
                "partitions": partitions,
            }

        total_complete = sum(
            r["complete_count"] for r in resolutions.values()
        )
        total_incomplete = sum(
            r["incomplete_count"] for r in resolutions.values()
        )
        return DataOpResult(
            ok=True,
            summary=(
                f"{total_complete} complete + {total_incomplete} incomplete "
                f"partitions across {len(resolutions)} resolutions"
            ),
            detail={
                "canonical": canonical,
                "asset_class": asset_class,
                "exists": True,
                "storage": storage.describe(),
                "resolutions": resolutions,
                "total_partitions_complete": total_complete,
                "total_partitions_incomplete": total_incomplete,
            },
        )
