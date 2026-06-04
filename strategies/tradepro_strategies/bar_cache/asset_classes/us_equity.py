"""US Equity (single-name) asset class plugin.

Covers US-listed common stocks (AAPL, MSFT, NVDA, AMZN, META, ...).
Calendar + bar-count semantics are IDENTICAL to ``us_etf`` — NYSE
sessions cover both listing types — so this class subclasses
``UsEtfPlugin`` and only overrides the registry identity. Keeping
the two distinct (rather than letting equities ride on the ETF
plugin name) preserves:

  * Provider preferences segregation. The data_source_preferences
    table can route ``us_equity, 1m`` to a different chain than
    ``us_etf, 1m`` if/when the trader wants single-name 1m bars from
    a paid feed but is happy with yfinance for ETFs.
  * Telemetry segregation. ``bar_cache_events.asset_class`` makes the
    cockpit panel show "single-name fetches" and "ETF fetches"
    separately, which matters because rate-limit pressure differs
    materially between the two.

If the calendars ever diverge (e.g. OTC + ADR sessions), the override
machinery is already in place — overriding ``expected_session_dates``
+ ``expected_bar_count`` is all it takes."""
from __future__ import annotations

from dataclasses import dataclass

from ..asset_class import register_asset_class
from .us_etf import UsEtfPlugin, _US_EQUITY_SCHEMA


@dataclass
class UsEquityPlugin(UsEtfPlugin):
    """Single-name US equity plugin. NYSE calendar + bar counts
    inherited from ``UsEtfPlugin``; only the registry identity
    changes."""

    name: str = "us_equity"
    display_name: str = "US Equity (single-name)"


# Auto-register on import.
register_asset_class(UsEquityPlugin())

# Re-export schema so callers consuming the asset_classes package have
# one canonical handle. Keeps imports stable even if we factor the
# NYSE bits out into a shared module later.
__all__ = ["UsEquityPlugin", "_US_EQUITY_SCHEMA"]
