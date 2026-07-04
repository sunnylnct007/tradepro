"""UNIVERSE_CORE — the small set of symbols the firm would actually trade, and the
ONLY list the Data Accuracy Turnaround (SPRINT.md, 2026-07-04) must drive to
broker-GOOD before anything on top of it is shown as actionable.

The one rule: nothing is actionable until the data under it is broker-GOOD for the
symbols it covers. "Done" for Phases 1-2 = every symbol here reads GOOD (not BRONZE)
on /api/admin/data-trust/bar-cache/quality for daily + hourly. Accuracy over
breadth: keep this list SMALL and provably true; everything outside it is explicitly
labeled "not trusted", never silently shown.

Composition: the most liquid US large-caps (deep, clean IBKR history + tight spreads)
plus the core ETFs we actually use. Deliberately ~30 names — small enough to backfill
from IBKR within its pacing limits and verify end-to-end, large enough to be a real
tradeable book. Grow ONLY by promoting a name that reads GOOD.
"""
from __future__ import annotations

# Mega-cap tech / growth — deepest liquidity, cleanest broker data.
_MEGA_TECH = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO",
]

# Liquid large-caps across sectors (financials, health, staples, energy, discretionary).
_LARGE_CAPS = [
    "JPM", "V", "MA", "BAC", "UNH", "LLY", "JNJ", "PG", "KO", "WMT",
    "COST", "HD", "MCD", "XOM", "CVX", "ORCL", "CRM", "NFLX", "DIS",
]

# Core ETFs we actually reference (broad market, tech, gold).
_CORE_ETFS = [
    "SPY", "QQQ", "GLD",
]

# The canonical list. Order is stable (mega → large → ETF) so diffs are readable.
UNIVERSE_CORE: list[str] = _MEGA_TECH + _LARGE_CAPS + _CORE_ETFS

# Fast membership check for gates ("is this symbol in the trusted core?").
UNIVERSE_CORE_SET: frozenset[str] = frozenset(UNIVERSE_CORE)


def is_core(symbol: str) -> bool:
    """True if `symbol` (bare ticker, case-insensitive) is in the trusted core."""
    return (symbol or "").strip().upper() in UNIVERSE_CORE_SET
