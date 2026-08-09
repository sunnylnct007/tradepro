"""Shared IBKR-golden-source daily bar fetch for strategies still migrating off
the legacy private yahoo-only cache (see [[feedback_ibkr_golden_source_yahoo_fallback]]).

One BarStore singleton, one fetch function — `ichimoku_equity.py`, `intraday_flat.py`
and `sector_rs.py` all had their own copy/near-copy of this fetch-with-fallback
logic; extracted here so there's one place to fix instead of three (see the
2026-08-03 SPY NaN incident: three independent regime-veto implementations,
only one of which got the NaN guard until this pass).

Reads the SAME shared ~/.tradepro/bar_cache the daily harvest daemon already
populates, via the `ibkr_web -> ibkr -> ig -> yfinance` provider chain (matches
the CLI harvester's default chain). Falls back to the legacy private
`cache.py` yahoo cache only if BarStore itself raises — that old path stays
until every consumer is migrated and it can be deleted outright.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

_log = logging.getLogger("tradepro.ibkr_bars")

_BAR_STORE = None  # lazy singleton — one BarStore per process


def bar_store():
    global _BAR_STORE
    if _BAR_STORE is not None:
        return _BAR_STORE
    from .bar_cache import asset_classes as _asset_classes  # noqa: F401 — registers plugins
    from .bar_cache.store import BarStore

    preferences_loader = None
    try:
        from .cli.push_to_api import load_credentials
        from .bar_cache.preferences import PreferencesLoader
        base, token = load_credentials()
        if base:
            preferences_loader = PreferencesLoader(api_base=base, auth_token=token)
    except Exception as exc:  # noqa: BLE001 — chain default covers this
        _log.debug("BarStore preferences loader unavailable, using default chain: %s", exc)

    _BAR_STORE = BarStore(
        base_dir=Path.home() / ".tradepro" / "bar_cache",
        provider_chain=["ibkr_web", "ibkr", "ig", "yfinance"],
        preferences_loader=preferences_loader,
    )
    return _BAR_STORE


def fetch_daily_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    asset_class: str = "us_equity",
    fetched_by: str = "unknown",
    legacy_provider: str = "yahoo",
) -> pd.DataFrame | None:
    """IBKR-first daily bar fetch with legacy-cache fallback. Returns None only
    if BOTH the BarStore chain and the legacy cache come back empty/erroring —
    callers keep their existing fail-open behaviour on None, unchanged."""
    try:
        frame = bar_store().get(
            symbol, asset_class, "1d", start, end,
            allow_partial=True, fetched_by=fetched_by,
        )
        if frame.df is not None and not frame.df.empty:
            if not frame.coverage_complete:
                _log.debug("BarStore partial coverage for %s (%s -> %s)",
                           symbol, start.date(), end.date())
            return frame.df
    except Exception as exc:  # noqa: BLE001 — fall through to legacy cache
        _log.warning("BarStore fetch failed for %s, falling back to legacy "
                     "%s cache: %s", symbol, legacy_provider, exc)

    try:
        from .cache import ensure_cached
        return ensure_cached(legacy_provider, symbol, start, end, interval="1d")
    except Exception as exc:  # noqa: BLE001
        _log.debug("legacy cache fetch failed for %s: %s", symbol, exc)
        return None


__all__ = ["bar_store", "fetch_daily_bars"]
