"""Trustworthy bar cache (Phase B-1).

Public entry point: ``BarStore``.

Design framing (see ROADMAP "Trustworthy data layer", Phase B):
  * Asset-class-pluggable — adding options / futures / crypto later is
    a single file under ``asset_classes/``.
  * Partial data fails loud — ``BarStore.get()`` either returns a
    complete frame or raises a structured ``BarFetchError``.
  * Provider chain ordered by the ``data_source_preferences`` table
    (migration 029); BarStore falls back along the chain on each fetch.
  * Atomic Parquet writes — write to tmp, fsync, rename. Never leave a
    partial file under the partition path.
  * Manifest per partition declares "what should be here"; reader
    refuses partial reads unless the caller opts in.
  * Structured telemetry — every fetch emits one bar_cache_events row
    (DB if reachable, JSONL fallback).
  * Per-symbol health record updated incrementally for the cockpit
    "is the data layer healthy?" panel (Phase G).

Phase B-1 (this PR) covers:
  * Core interfaces + ``BarStore`` orchestration
  * Asset class plugin: ``us_etf``
  * Provider: ``yfinance``
  * Operator CLI: ``tradepro-bar-cache-get``
  * BDD scenarios covering the happy path + the failure modes

Phase B-2 (separate PR) will migrate ``intraday_flat`` to opt in.
Phase B-3 reads ``data_source_preferences`` to drive the chain
dynamically (today the chain is hardcoded ``[yfinance]`` so it works
without backend wiring).
"""
from __future__ import annotations

from .errors import (
    BarFetchError,
    ManifestViolation,
    NoProviderAvailableError,
    ProviderNetworkError,
    ProviderParseError,
    ProviderRateLimitError,
    SchemaVersionMismatch,
)
from .manifest import Manifest
from .store import BarStore, BarFrame

__all__ = [
    "BarFetchError",
    "BarFrame",
    "BarStore",
    "Manifest",
    "ManifestViolation",
    "NoProviderAvailableError",
    "ProviderNetworkError",
    "ProviderParseError",
    "ProviderRateLimitError",
    "SchemaVersionMismatch",
]
