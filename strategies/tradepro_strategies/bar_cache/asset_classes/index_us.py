"""US index asset class plugin (^VIX, ^TNX, ^GSPC, ...).

Added 22 Aug 2026 to give index CONTEXT data a proper home in the canonical
store. Until now ^VIX/^TNX lived only in the legacy yahoo cache (the last
blocker on retiring it), and misfiled index dirs in the equity trees rendered
as buyable rows on trading screens.

Calendar: NYSE sessions (CBOE/US indices publish on the same days), so this
subclasses ``UsEtfPlugin`` like ``us_equity`` does. Two deliberate
differences:

  * ``_flat_phantom_guard = False`` — indices report volume 0 on every bar
    by nature; the flat-phantom rejection (identical close + zero volume)
    would misread a flat-yield week on ^TNX as wrong-contract poison.
  * These are CONTEXT series, never tradeable instruments. Anything
    deriving a tradeable universe must continue to exclude ``^`` names
    (the screens' _tradeable() guard + the harvest filter both do).

Provider reality: IBKR's Web API path searches secType=STK, which cannot
resolve an index, so the chain falls through to yfinance — an honest,
visible bronze. If index data ever needs golden sourcing, the backend needs
a secType=IND search variant first.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..asset_class import register_asset_class
from .us_etf import UsEtfPlugin, _US_EQUITY_SCHEMA


@dataclass
class IndexUsPlugin(UsEtfPlugin):
    """US index plugin — NYSE calendar, zero-volume bars are legitimate."""

    name: str = "index_us"
    display_name: str = "US index (context data)"

    # Indices print volume 0 on every bar; see module docstring.
    _flat_phantom_guard: bool = False


register_asset_class(IndexUsPlugin())

__all__ = ["IndexUsPlugin", "_US_EQUITY_SCHEMA"]
