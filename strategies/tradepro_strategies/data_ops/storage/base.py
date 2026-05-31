"""``BarCacheStorage`` interface.

The minimum surface a handler needs to introspect / mutate the cache
without knowing what's underneath. Designed to be implementable
against:
  * Local disk (today, ``LocalBarCacheStorage``)
  * S3 (Phase I; reads via boto3, writes via multipart upload)
  * A network share / NFS (no implementation needed; LocalBarCacheStorage
    points at the mount)
  * A test fixture (in-memory; the BDD uses ``LocalBarCacheStorage``
    with a tmpdir for simplicity)

Operations are intentionally narrow — list manifests, read a single
manifest, check whether a partition exists. Bulk writes / fetches
stay in the BarStore (which is separate from data_ops); the handlers
here are about observability + maintenance, not the hot path.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator


class BarCacheStorage(ABC):
    """Abstract storage backend for the trustworthy bar cache.

    Naming: ``read_manifest`` returns the parsed Manifest dataclass
    (we import lazily inside the implementation to avoid a circular
    import — the bar_cache package depends on this one is the wrong
    direction; data_ops depends on bar_cache via the read path).
    """

    @abstractmethod
    def symbol_exists(self, asset_class: str, canonical: str) -> bool:
        """True if the storage backend has any data for this symbol."""

    @abstractmethod
    def list_resolutions(
        self, asset_class: str, canonical: str,
    ) -> list[str]:
        """Resolutions the symbol has at least one partition for.
        Empty list when the symbol exists but no resolutions are
        populated yet (rare; usually symbol_exists=False covers it)."""

    @abstractmethod
    def list_manifests(
        self, asset_class: str, canonical: str, resolution: str,
    ) -> Iterator[tuple[str, "ManifestLike"]]:
        """Yields ``(partition_id, manifest)`` tuples for every
        partition in the (asset_class, canonical, resolution)
        directory. The handler iterates this to build a gap report.
        ``manifest`` is the parsed Manifest from ``bar_cache.manifest``
        (typed at runtime by the implementation, not by this
        interface, to keep the import direction one-way).
        """

    @abstractmethod
    def describe(self) -> dict[str, str]:
        """Free-form descriptor for telemetry. Local backend reports
        ``{"backend": "local", "base_dir": "/path/..."}``; S3 backend
        will report ``{"backend": "s3", "bucket": "..."}``. The
        cockpit shows this so an operator knows which storage the
        worker is reading."""


class ManifestLike:
    """Marker type alias — implementations return the real
    ``bar_cache.manifest.Manifest`` dataclass at runtime. The marker
    avoids the import-direction issue with the bar_cache package
    while still giving callers a type hint they can substitute."""
