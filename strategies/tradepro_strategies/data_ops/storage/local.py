"""Local-filesystem implementation of ``BarCacheStorage``.

The default backend today. Reads the same directory layout the
``BarStore`` writes to:

    <base_dir>/<asset_class>/<canonical>/<resolution>/<partition>.parquet
    <base_dir>/<asset_class>/<canonical>/<resolution>/<partition>.manifest.json

Multi-machine note: this implementation works fine when ``base_dir``
points at a network mount (NFS, EBS) shared between the strategy
host and the data-worker host. The S3 implementation is a separate
class (Phase I) — keeping them split prevents accidental "did this
read just hit S3?" surprises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import BarCacheStorage


class LocalBarCacheStorage(BarCacheStorage):
    """Filesystem-backed storage. ``base_dir`` defaults to
    ``~/.tradepro/bar_cache`` to match the BarStore default; tests
    pass a tmpdir."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)

    def symbol_exists(self, asset_class: str, canonical: str) -> bool:
        return (self.base_dir / asset_class / canonical).is_dir()

    def list_resolutions(
        self, asset_class: str, canonical: str,
    ) -> list[str]:
        symbol_dir = self.base_dir / asset_class / canonical
        if not symbol_dir.is_dir():
            return []
        return sorted(
            child.name for child in symbol_dir.iterdir() if child.is_dir()
        )

    def list_manifests(
        self, asset_class: str, canonical: str, resolution: str,
    ):
        # Lazy import — data_ops can be imported without bar_cache in
        # a pure-test environment. Real users always have both.
        from tradepro_strategies.bar_cache.manifest import Manifest

        res_dir = self.base_dir / asset_class / canonical / resolution
        if not res_dir.is_dir():
            return
        for mf_path in sorted(res_dir.glob("*.manifest.json")):
            partition = mf_path.stem.replace(".manifest", "")
            try:
                manifest = Manifest.read(mf_path)
            except Exception as exc:  # noqa: BLE001
                yield partition, _UnreadableManifest(mf_path, exc)
                continue
            yield partition, manifest

    def describe(self) -> dict[str, str]:
        return {"backend": "local", "base_dir": str(self.base_dir)}


class _UnreadableManifest:
    """Sentinel for the rare case of a present-but-corrupt manifest
    file. Carries the path + exception so the handler can report
    'partition X has a corrupt manifest' rather than crash. Handlers
    check ``isinstance(manifest, _UnreadableManifest)`` before
    accessing schema fields."""

    def __init__(self, path: Path, exc: Exception) -> None:
        self.path = path
        self.exc = exc

    def __repr__(self) -> str:
        return f"<UnreadableManifest path={self.path} exc={self.exc}>"
