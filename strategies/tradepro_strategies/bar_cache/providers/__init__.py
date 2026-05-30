"""Provider plugins for the bar cache.

A provider knows how to talk to ONE data source (yfinance, IG /prices,
finnhub, polygon, etc.). The BarStore composes them into a chain
ordered by the ``data_source_preferences`` table.

Each provider:
  * Implements ``Provider.fetch()`` returning a normalised DataFrame.
  * Raises typed ``BarFetchError`` subclasses on failure.
  * Declares its history depth via ``max_history()`` so the chain
    can skip a provider that can't serve a request before bothering
    to call it.
"""
from __future__ import annotations

from .base import Provider, register_provider, get_provider, list_providers
from .yfinance_provider import YFinanceProvider

__all__ = [
    "Provider",
    "register_provider",
    "get_provider",
    "list_providers",
    "YFinanceProvider",
]
