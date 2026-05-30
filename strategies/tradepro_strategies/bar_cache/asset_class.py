"""Asset-class plugin protocol + registry.

Each asset class declares:
  * Its schema (column set + dtypes + schema_version string)
  * The calendar (which days are sessions, when do they open/close)
  * The integrity rules (expected bars per session at a given resolution)
  * The partition strategy (which timestamps go in which file)
  * The resolutions it supports

This split is what makes the BarStore asset-class-agnostic. Adding
options or futures later is a new module under ``asset_classes/``;
the store, manifest, telemetry, CLI all stay unchanged.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class BarSchema:
    """Column contract for an asset class. The actual Parquet
    schema is built from this in ``BarStore.write``. ``column_order``
    matters — Parquet reads are positional in places, so a stable
    order avoids subtle bugs."""
    schema_version: str
    column_order: tuple[str, ...]
    required_columns: frozenset[str]
    nullable_columns: frozenset[str] = frozenset()


class AssetClassPlugin(ABC):
    """Interface every asset-class plugin implements.

    All methods are pure — no I/O, no state. The BarStore composes
    these methods with provider fetches; the plugin just declares
    the rules for its asset class."""

    name: str           # registry key, e.g. "us_etf"
    display_name: str   # human-readable, "US Equity ETF"
    schema: BarSchema

    @abstractmethod
    def supported_resolutions(self) -> tuple[str, ...]:
        """Resolutions the plugin will accept fetches for. Anything
        else raises before the provider is even called."""

    @abstractmethod
    def partition_key(self, ts: datetime) -> str:
        """Group ``ts`` into a partition identifier. For most asset
        classes this is the year-month string ("2024-12"); options
        chains partition by underlying + expiry instead."""

    @abstractmethod
    def expected_session_dates(self, start: datetime, end: datetime) -> list[date]:
        """Trading-session dates between ``start`` and ``end``
        inclusive. Used by the manifest's expected-vs-actual check."""

    @abstractmethod
    def expected_bar_count(self, resolution: str, session_date: date) -> int:
        """Bars expected on a single session at a resolution. e.g.
        390 for US equity 1m on a full session, 78 on a half-day.
        The store sums across sessions to get the total expected."""

    @abstractmethod
    def validate_frame(self, df: pd.DataFrame) -> None:
        """Raise ``ProviderParseError``-shaped errors if the dataframe
        violates the schema. Called immediately after a provider
        fetch; catches drift early before the parquet write."""


# ---- Registry ----------------------------------------------------------

_REGISTRY: dict[str, AssetClassPlugin] = {}


def register_asset_class(plugin: AssetClassPlugin) -> AssetClassPlugin:
    """Add a plugin to the in-process registry. Idempotent — re-
    registration overwrites. Use ``get_asset_class(name)`` to look
    up; raises ``KeyError`` on unknown name (fail-loud is the rule)."""
    _REGISTRY[plugin.name] = plugin
    return plugin


def get_asset_class(name: str) -> AssetClassPlugin:
    """Look up a registered plugin by name. Raises ``KeyError`` if
    unknown — silent fallback would mask asset-class typos."""
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown asset class {name!r}; registered: "
            f"{sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def list_asset_classes() -> list[str]:
    """Names of every registered plugin. The CLI + future UI use this
    to enumerate available asset classes."""
    return sorted(_REGISTRY.keys())


def _clear_registry_for_tests() -> None:
    """Test-only — reset the registry between BDD scenarios so a test
    that adds a synthetic asset class doesn't leak into the next."""
    _REGISTRY.clear()
