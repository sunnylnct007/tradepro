"""Asset-class plugins.

Importing this package registers every built-in plugin (the same
side-effect pattern ``paper/strategies/__init__.py`` uses for the
strategy registry). The Phase A BDD regression guard (registry
coherence) applies here too: anything added to the production
provider chain must be imported here.
"""
from __future__ import annotations

from .us_etf import UsEtfPlugin  # noqa: F401 — registers

__all__ = ["UsEtfPlugin"]
