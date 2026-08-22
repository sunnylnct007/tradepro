"""The paper engine's bar bus must read the CANONICAL store first.

22 Aug 2026: the default chain was CachedSource(YfinanceSource()) whose
docstring claimed the cache it reads "is what the IBKR harvest populates".
False — CachedSource reads ~/.tradepro/cache/intraday/ (last written 8 Jul
2026) while the harvest fills ~/.tradepro/bar_cache/. A live paper session
therefore decided on IBKR-golden closes but took its trigger bars and fill
marks from live Yahoo: two data paths, one strategy.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from tradepro_strategies.paper.profiles import default_bar_source
from tradepro_strategies.paper.sources import BarStoreSource


def test_golden_store_is_first_in_the_default_chain():
    chain = default_bar_source()
    names = [getattr(s, "name", type(s).__name__) for s in chain.sources]
    assert names[0] == "bar_store", (
        f"canonical store must be consulted FIRST, got {names}")
    # Yahoo stays reachable — an end-of-session top-up the harvest has not
    # written yet is a real case; it must remain a VISIBLE fallback.
    assert len(names) > 1, "the live fallback must not be removed"


def test_unknown_symbol_returns_empty_rather_than_raising():
    # FallbackSource walks the chain on an EMPTY list; a raising source
    # would abort the session instead of degrading to the next one.
    bars = asyncio.run(BarStoreSource().fetch(
        "SYMBOL_THAT_CANNOT_EXIST", datetime(2026, 8, 21, tzinfo=timezone.utc), "1d"))
    assert bars == []


def test_unsupported_interval_returns_empty():
    bars = asyncio.run(BarStoreSource().fetch(
        "SPY", datetime(2026, 8, 21, tzinfo=timezone.utc), "7s"))
    assert bars == []


def test_never_fetches_on_a_trading_tick(monkeypatch):
    """The paper bus must NEVER trigger a provider fetch — a rate-limit
    stall here delays an order. Pinned by asserting skip_fetch is passed."""
    seen: dict = {}

    class _FakeFrame:
        df = None

    class _FakeStore:
        def get(self, **kwargs):
            seen.update(kwargs)
            return _FakeFrame()

    import tradepro_strategies.ibkr_bars as ib
    monkeypatch.setattr(ib, "bar_store", lambda: _FakeStore())
    asyncio.run(BarStoreSource().fetch(
        "SPY", datetime(2026, 8, 21, tzinfo=timezone.utc), "1d"))
    assert seen.get("skip_fetch") is True, (
        "paper bus must read cache-only; a fetch on a trading tick can stall an order")
    assert seen.get("allow_partial") is True
