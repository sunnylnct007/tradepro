"""BarStoreSource — the paper engine reading the CANONICAL bar store.

Why this exists (22 Aug 2026). The paper engine's default chain was
``CachedSource(YfinanceSource())``, and its docstring claimed the cache it
read "is what the IBKR harvest populates". That was false: ``CachedSource``
reads ``~/.tradepro/cache/intraday/`` — a separate parquet tree whose last
writer ran 8 Jul 2026 — while the harvest fills ``~/.tradepro/bar_cache/``
(the BarStore). So a live paper session decided on IBKR-golden closes
(ichimoku_equity pulls its own history through ``ibkr_bars``) but had its
trigger bars and fill marks served by live Yahoo. Two data paths, one
strategy — the exact divergence the one-source-of-truth work exists to end.

This source puts the canonical store FIRST in the chain. It never fetches:
a session absent from the store returns ``[]`` so ``FallbackSource`` walks
on to the Yahoo path, which stays as the visible last resort (an
end-of-session top-up the harvest has not written yet is a real case).

Deliberately read-only — no ``force_refresh``, no provider chain walk. The
paper engine must never trigger an IBKR fetch on a trading tick; harvest
lanes own that, and this is the surface where a rate-limit stall would
delay an order.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..strategy import Bar
from .base import BarSource

log = logging.getLogger("tradepro.paper.sources.bar_store")

# interval label → (bar_cache resolution, seconds per bar)
_INTERVAL: dict[str, tuple[str, int]] = {
    "1m": ("1m", 60), "60s": ("1m", 60),
    "5m": ("5m", 300), "300s": ("5m", 300),
    "15m": ("15m", 900),
    "30m": ("30m", 1800),
    "1h": ("1h", 3600), "60m": ("1h", 3600),
    "1d": ("1d", 86400), "1day": ("1d", 86400),
}


@dataclass
class BarStoreSource(BarSource):
    """Serve one session's bars out of the canonical BarStore. Read-only."""

    name: str = "bar_store"
    asset_class: str = "us_etf"   # the single canonical tree
    _warned: set = field(default_factory=set)

    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        mapped = _INTERVAL.get(str(interval).lower())
        if mapped is None:
            return []
        resolution, tf_seconds = mapped
        try:
            from ...bar_cache import BarStore
            from ...bar_cache import asset_classes as _ac  # noqa: F401 — registers
            from ...ibkr_bars import bar_store

            store: BarStore = bar_store()
            day = session_date.replace(hour=0, minute=0, second=0, microsecond=0)
            if day.tzinfo is None:
                day = day.replace(tzinfo=timezone.utc)
            frame = store.get(
                canonical=symbol,
                asset_class=self.asset_class,
                resolution=resolution,
                start=day,
                end=day + timedelta(days=1),
                allow_partial=True,
                skip_fetch=True,          # never fetch on a trading tick
                fetched_by="paper-bus",
            )
        except Exception as exc:  # noqa: BLE001 — a source NEVER raises
            if symbol not in self._warned:
                self._warned.add(symbol)
                log.debug("bar_store source unavailable for %s: %s", symbol, exc)
            return []

        df = getattr(frame, "df", None)
        if df is None or df.empty:
            return []
        out: list[Bar] = []
        for ts, row in df.iterrows():
            try:
                out.append(Bar(
                    symbol=symbol,
                    timestamp=ts.to_pydatetime(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=int(row["volume"]) if row["volume"] == row["volume"] else 0,
                    timeframe_seconds=tf_seconds,
                ))
            except (KeyError, TypeError, ValueError):
                continue   # a malformed row is skipped, never fatal
        if out:
            log.debug("bar_store served %s %s %s (%d bars)",
                      symbol, day.date(), resolution, len(out))
        return out


__all__ = ["BarStoreSource"]
