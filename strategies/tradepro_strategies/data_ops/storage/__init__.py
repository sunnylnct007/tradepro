"""Storage abstraction for the bar cache — the seam where
multi-service / multi-machine deployments swap implementations.

Production realities the project is heading toward (per
ROADMAP "Trustworthy data layer" → Phase I — S3 hybrid):

  * Hot working set on local disk (today, ``LocalBarCacheStorage``)
  * Warm archive in S3 IA (Phase I, ``S3BarCacheStorage`` — not in
    this PR, but the seam is in place)
  * Cold archive in S3 Glacier Deep Archive (Phase I+)

Different services may live on different hosts. The data-worker
running on a build host doesn't have access to the trader's local
``~/.tradepro/bar_cache/``; it talks to whatever ``BarCacheStorage``
implementation is wired by the deployment.

Today only ``LocalBarCacheStorage`` exists; adding ``S3BarCacheStorage``
is a single new module under this package. Every handler accepts
``BarCacheStorage`` so swapping is config, not refactoring.
"""
from __future__ import annotations

from .base import BarCacheStorage
from .local import LocalBarCacheStorage

__all__ = [
    "BarCacheStorage",
    "LocalBarCacheStorage",
]
