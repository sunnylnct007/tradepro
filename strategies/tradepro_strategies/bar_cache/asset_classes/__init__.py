"""Asset-class plugins.

Importing this package registers every built-in plugin (the same
side-effect pattern ``paper/strategies/__init__.py`` uses for the
strategy registry). The Phase A BDD regression guard (registry
coherence) applies here too: anything added to the production
provider chain must be imported here.

Plugin set (post asset-class breadth expansion):
  * ``us_etf``     — SPY, QQQ, IWM, GLD, ... (NYSE calendar)
  * ``us_equity``  — AAPL, MSFT, NVDA, ... single names (NYSE calendar)
  * ``uk_equity``  — BARC.L, VOD.L, ... LSE-listed (LSE calendar)
  * ``fx``         — EUR/USD, GBP/USD, ... 24/5 spot FX
  * ``crypto``     — BTC-USD, ETH-USD, ... 24/7 spot

Adding a new asset class: drop the file under this directory + add
the import here. ``data_source_preferences`` should also seed a chain
for the new (asset_class × resolution) pair so the live data layer
knows which provider to try first.
"""
from __future__ import annotations

from .crypto import CryptoPlugin       # noqa: F401 — registers
from .fx import FxPlugin               # noqa: F401 — registers
from .index_uk import IndexUkPlugin    # noqa: F401 — registers
from .index_us import IndexUsPlugin    # noqa: F401 — registers
from .uk_equity import UkEquityPlugin  # noqa: F401 — registers
from .us_equity import UsEquityPlugin  # noqa: F401 — registers
from .us_etf import UsEtfPlugin        # noqa: F401 — registers

__all__ = [
    "CryptoPlugin",
    "FxPlugin",
    "UkEquityPlugin",
    "UsEquityPlugin",
    "UsEtfPlugin",
]
