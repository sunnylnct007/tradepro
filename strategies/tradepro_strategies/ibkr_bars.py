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
import os
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
        # `ibkr` (local Gateway) omitted by default — retired 9 Aug 2026, and
        # every attempt costs a ConnectionRefused round-trip per symbol.
        provider_chain=(["ibkr_web"]
                        + (["ibkr"] if os.environ.get(
                            "TRADEPRO_USE_LOCAL_GATEWAY", "0").strip().lower()
                            in ("1", "true", "yes", "on") else [])
                        + ["ig", "yfinance"]),
        preferences_loader=preferences_loader,
    )
    return _BAR_STORE


def golden_daily(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    fetched_by: str,
    legacy_provider: str = "yahoo",
) -> pd.DataFrame:
    """Drop-in replacement for the legacy ``cache.ensure_cached`` contract
    (returns a DataFrame, possibly EMPTY, never None) but golden-first:
    ibkr_web → ig → yfinance, legacy yahoo cache as the loudly-logged last
    resort. Added 22 Aug 2026 for the Wave-1 legacy-cache retirement — the
    MCP analysis tools, run_backtest, build_high_beta_universe and the
    worker all read the ≤7-day-stale yahoo cache while fresh IBKR bars sat
    unread in the store."""
    # Let the RESOLVER decide the tree (22 Aug 2026). Hardcoding us_etf sent
    # ^VIX to a NYSE-equity calendar and LSE ETFs (VUSA.L, VWRL.L …) to a
    # tree whose session expectations they can never satisfy — which is what
    # made those 14 symbols spam the run log with "missing days" every night.
    # us_equity resolves to the canonical us_etf tree (the write fork is
    # retired); everything else goes to its proper home.
    from .bar_cache.asset_class_resolver import resolve_asset_class
    _resolved = resolve_asset_class(symbol)
    asset_class = "us_etf" if _resolved in ("us_equity", "us_etf", "unknown") else _resolved
    df = fetch_daily_bars(symbol, start, end, asset_class=asset_class,
                          fetched_by=fetched_by, legacy_provider=legacy_provider)
    return df if df is not None else pd.DataFrame()


def fetch_daily_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    # ONE canonical tree (owner ruling 22 Aug 2026): "us_etf" is the
    # everything-bucket every scheduled lane harvests, mirrors to S3, and
    # audits. The old "us_equity" default silently forked 255 symbols'
    # daily bars into a second unharvested tree with zero behavioural
    # difference between the plugins (UsEquityPlugin only renames
    # UsEtfPlugin) — the wrong-venue poison had to be remediated TWICE
    # because of it. us_equity remains registered + readable; nothing
    # writes to it by default any more.
    asset_class: str = "us_etf",
    fetched_by: str = "unknown",
    legacy_provider: str = "yahoo",
) -> pd.DataFrame | None:
    """IBKR-first daily bar fetch with legacy-cache fallback. Returns None only
    if BOTH the BarStore chain and the legacy cache come back empty/erroring —
    callers keep their existing fail-open behaviour on None, unchanged."""
    df, _ = fetch_daily_bars_with_provenance(
        symbol, start, end, asset_class=asset_class,
        fetched_by=fetched_by, legacy_provider=legacy_provider)
    return df


def bars_provenance(df: "pd.DataFrame | None", *,
                    provider_used: str | None = None,
                    coverage_complete: bool | None = None,
                    rows_expected: int | None = None,
                    legacy: bool = False,
                    error: str | None = None) -> dict:
    """Describe where a daily-bar frame actually CAME FROM.

    The subtlety worth stating: ``BarStore.provider_used`` is ``"cache"`` when
    every partition was a hit — true, and useless for the owner's question.
    The bars themselves carry a per-bar ``source`` column naming who ORIGINALLY
    produced them, so that is what we report. A frame whose recent bars are a
    MIX of providers says so rather than picking one.

    On coverage: ``BarStore.coverage_complete`` is False for a shortfall of a
    SINGLE bar in 276 (its expected-row count is a calendar estimate, so a
    holiday alone trips it). Reporting that as "INCOMPLETE" would put a scare
    label on every healthy row — the cry-wolf failure the first data-readiness
    build made and had to be corrected for (9255cc5). So the shortfall is
    reported as a COUNT and graded by `missing_bars`; the caller decides what
    is material.

    Returns a plain dict (JSON-safe) — the caller wraps it in the uniform
    `provenance.describe()` shape."""
    if df is None or getattr(df, "empty", True):
        return {"source": None, "as_of": None, "rows": 0, "mixed": False,
                "sources": [], "provider_used": provider_used,
                "coverage_complete": coverage_complete,
                "rows_expected": rows_expected, "missing_bars": None,
                "tail_counts": {}, "tail_n": 0,
                "error": error, "legacy": legacy}

    as_of = None
    try:
        last = df.index[-1]
        as_of = last.isoformat() if hasattr(last, "isoformat") else str(last)
    except Exception:  # noqa: BLE001 — an odd index must not kill the row
        as_of = None

    # Grade on the RECENT tail, not the whole 400-day history: bars from 2024
    # having come from yahoo says nothing about whether today's close is golden.
    sources: list[str] = []
    tail_counts: dict[str, int] = {}
    tail_n = 0
    if "source" in getattr(df, "columns", []):
        tail = df["source"].dropna().tail(20)
        tail_n = len(tail)
        sources = [str(s) for s in dict.fromkeys(tail.tolist())]
        for s in tail.tolist():
            tail_counts[str(s)] = tail_counts.get(str(s), 0) + 1
    source = None
    if len(sources) == 1:
        source = sources[0]
    elif sources:
        # Mixed tail — name the one that produced the LAST bar, and flag it.
        source = sources[-1]
    elif legacy:
        source = "legacy_yahoo_cache"

    missing = None
    if rows_expected is not None and rows_expected > 0:
        missing = max(0, rows_expected - len(df))

    return {
        "source": source,
        "as_of": as_of,
        "rows": len(df),
        "rows_expected": rows_expected,
        "missing_bars": missing,
        "mixed": len(sources) > 1,
        "sources": sources,
        # Per-source counts over the graded tail — so a mixed history can be
        # reported as "10 of the last 20 from yfinance" instead of the useless
        # "some of these came from somewhere else".
        "tail_counts": tail_counts,
        "tail_n": tail_n,
        "provider_used": provider_used,
        "coverage_complete": coverage_complete,
        "error": error,
        "legacy": legacy,
    }


def fetch_daily_bars_with_provenance(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    asset_class: str = "us_etf",   # one canonical tree — see fetch_daily_bars
    fetched_by: str = "unknown",
    legacy_provider: str = "yahoo",
) -> "tuple[pd.DataFrame | None, dict]":
    """As `fetch_daily_bars`, but also returns who served the bars.

    Callers that put a number in front of a human should use THIS one: the
    plain variant discards the origin, which is how the screen spent a week
    unable to say whether a close came from IBKR, from yahoo, or from a cache
    partition written days earlier."""
    try:
        frame = bar_store().get(
            symbol, asset_class, "1d", start, end,
            allow_partial=True, fetched_by=fetched_by,
        )
        if frame.df is not None and not frame.df.empty:
            if not frame.coverage_complete:
                _log.debug("BarStore partial coverage for %s (%s -> %s)",
                           symbol, start.date(), end.date())
            return frame.df, bars_provenance(
                frame.df, provider_used=frame.provider_used,
                coverage_complete=frame.coverage_complete,
                rows_expected=frame.rows_expected)
        store_error = "bar store returned no rows"
    except Exception as exc:  # noqa: BLE001 — fall through to legacy cache
        store_error = str(exc)[:200]
        _log.warning("BarStore fetch failed for %s, falling back to legacy "
                     "%s cache: %s", symbol, legacy_provider, exc)

    try:
        from .cache import ensure_cached
        df = ensure_cached(legacy_provider, symbol, start, end, interval="1d")
        # Reaching here at all is notable: the golden chain gave nothing and we
        # are serving the private yahoo cache. Say so loudly — a silent yahoo
        # default is the exact failure the owner has flagged every session.
        if df is not None and not getattr(df, "empty", True):
            _log.warning("%s: serving bars from the LEGACY %s cache — the "
                         "golden chain gave nothing (%s)",
                         symbol, legacy_provider, store_error)
        return df, bars_provenance(df, legacy=True, error=store_error)
    except Exception as exc:  # noqa: BLE001
        _log.debug("legacy cache fetch failed for %s: %s", symbol, exc)
        return None, bars_provenance(None, legacy=True,
                                     error=f"{store_error}; legacy: {str(exc)[:120]}")


__all__ = ["bar_store", "fetch_daily_bars", "fetch_daily_bars_with_provenance",
           "bars_provenance"]
