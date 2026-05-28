"""Wikipedia-driven symbol-universe registry + scrapers.

See ``wikipedia.py`` for the active registry. This package exposes
``fetch_universe``, ``fetch_all_universes``, ``WIKI_UNIVERSES``, and
``UniverseFetchError`` so callers don't have to know the module layout.
"""
from .wikipedia import (
    WIKI_UNIVERSES,
    Symbol,
    UniverseDef,
    UniverseFetchError,
    fetch_all_universes,
    fetch_universe,
    parse_universe_html,
)

__all__ = [
    "WIKI_UNIVERSES",
    "Symbol",
    "UniverseDef",
    "UniverseFetchError",
    "fetch_all_universes",
    "fetch_universe",
    "parse_universe_html",
]
