"""Provider protocol + registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

import pandas as pd


class Provider(ABC):
    """One data source. The BarStore composes providers into a chain;
    each is asked in order until one returns data."""

    name: str   # registry key, e.g. "yfinance"

    @abstractmethod
    def fetch(
        self,
        canonical: str,
        asset_class: str,
        resolution: str,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Pull bars for the (canonical, resolution, range) tuple.

        Returns ``(df, metadata)`` where:
          * ``df`` is a DataFrame with the asset-class columns. Index
            is the bar timestamp (UTC, tz-aware). Empty DataFrame is
            allowed for "no data in range" (provider OK, range empty).
          * ``metadata`` is a free-form dict for telemetry — at
            minimum {"provider_version": "...", "rows": N}. Optional
            keys like {"adjustment_source": "split-adjusted"} surface
            in the manifest for the audit trail.

        Raises one of:
          * ``ProviderRateLimitError`` on 429 / quota-exceeded.
          * ``ProviderNetworkError`` on connection / timeout failure.
          * ``ProviderParseError`` on schema-drift / unexpected nulls.

        Never returns a partial-with-warning result silently; raise
        with structured fields so the chain can decide."""

    @abstractmethod
    def max_history(self, resolution: str) -> timedelta | None:
        """How far back in time this provider can serve at this
        resolution. ``None`` means "unlimited" (e.g. yfinance daily
        goes back ~20 years; we treat that as unlimited).

        The BarStore uses this to skip a provider that can't satisfy
        the requested range before even calling it. yfinance 1-minute
        returns ``timedelta(days=7)`` — the documented ceiling."""

    def supports_resolution(self, resolution: str) -> bool:
        """Default true; override when a provider only supports a
        subset. The chain skips unsupported (rather than letting
        ``fetch`` raise) so the telemetry log shows the provider was
        not attempted, not that it errored."""
        return True


# ---- Registry --------------------------------------------------------

_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> Provider:
    _REGISTRY[provider.name] = provider
    return provider


def get_provider(name: str) -> Provider:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown provider {name!r}; registered: "
            f"{sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def list_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


def _clear_registry_for_tests() -> None:
    _REGISTRY.clear()
