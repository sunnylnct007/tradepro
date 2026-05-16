"""FallbackSource — try a chain of `BarSource`s; the first non-empty
result wins.

Use case: Yahoo's intraday endpoint occasionally 404s or 429s, and
its 1m window is only 60 days. Finnhub (and future Polygon/Alpaca)
provide overlapping coverage. Stacking them behind a fallback means
a temporary Yahoo blip doesn't abort a backtest, and historical
sessions older than 60 days can still source bars from one of the
deeper providers.

Logging is deliberately loud — every miss + fallback transition is
recorded at INFO so the operator can see "Yahoo missed for AAPL
2026-01-05; Finnhub filled it in". If every source returns empty,
the bus emits zero bars and the engine completes the session as a
no-op (preserves the engine's "missing data should never crash a
strategy" invariant).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..strategy import Bar
from .base import BarSource


log = logging.getLogger("tradepro.paper.sources.fallback")


@dataclass
class FallbackSource(BarSource):
    """List of sources, tried in order. Each source is given the
    same (symbol, session_date, interval) tuple. The FIRST one that
    returns a non-empty list wins."""

    sources: list[BarSource] = field(default_factory=list)
    name: str = "fallback_source"

    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        for source in self.sources:
            try:
                bars = await source.fetch(symbol, session_date, interval)
            except Exception:
                log.exception(
                    "source %s raised on %s %s %s — continuing chain",
                    getattr(source, "name", type(source).__name__),
                    symbol, session_date.date(), interval,
                )
                continue
            if bars:
                if source is not self.sources[0]:
                    log.info(
                        "fallback %s served %s %s %s (%d bars)",
                        getattr(source, "name", "?"),
                        symbol, session_date.date(), interval, len(bars),
                    )
                return bars
        log.warning(
            "no source returned bars for %s %s %s — all %d sources empty",
            symbol, session_date.date(), interval, len(self.sources),
        )
        return []


__all__ = ["FallbackSource"]
