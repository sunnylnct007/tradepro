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
    returns a non-empty list wins.

    After a successful fetch, ``last_source`` holds the name of the
    provider that actually served the bars (e.g. "ibkr", "yfinance",
    "finnhub"). It is updated on every ``fetch()`` call that returns
    non-empty bars so callers can surface data-provenance to the UI.
    """

    sources: list[BarSource] = field(default_factory=list)
    name: str = "fallback_source"
    last_source: str = field(default="", init=False)

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
                source_name = getattr(source, "name", type(source).__name__)
                self.last_source = source_name
                if source is not self.sources[0]:
                    log.info(
                        "fallback %s served %s %s %s (%d bars)",
                        source_name,
                        symbol, session_date.date(), interval, len(bars),
                    )
                return bars
        _record_empty(symbol, str(session_date.date()), interval,
                      len(self.sources))
        return []


__all__ = ["FallbackSource"]


# ---------------------------------------------------------------------------
# EMPTY-FETCH REPORTING: one line per run, not one per symbol.
#
# Measured 3 Sep 2026: the equity daemon's log grew ~900 MB in a single day and
# rotate-logs.sh truncates to the last 20 MB, so a day's history was being
# discarded within hours. 2,240 of those lines per run were this warning and
# yfinance's twin, once per symbol — 1,106 identical messages saying the US
# market had not opened yet.
#
# That volume did real harm beyond disk. It buried the ONE thing an operator
# needs to see: ichimoku_equity made no entries all day, and the reason was
# that every source returned nothing, not that the rule declined. A warning
# repeated 1,106 times is indistinguishable from wallpaper.
#
# So: count per (date, interval), keep the per-symbol detail at DEBUG, and emit
# a SINGLE warning naming the count and a sample. The count is the point — a
# handful of silent names overnight is normal; all 244 is a broken feed, and
# only the number tells them apart.
_EMPTY: dict[tuple[str, str], list[str]] = {}
_EMPTY_SOURCES: dict[tuple[str, str], int] = {}


def _record_empty(symbol: str, day: str, interval: str, n_sources: int) -> None:
    key = (day, interval)
    _EMPTY.setdefault(key, []).append(symbol)
    _EMPTY_SOURCES[key] = n_sources
    log.debug("no source returned bars for %s %s %s — all %d sources empty",
              symbol, day, interval, n_sources)


def flush_empty_summary() -> None:
    """Emit one warning per (date, interval) that had empty fetches."""
    for (day, interval), syms in sorted(_EMPTY.items()):
        shown = ", ".join(syms[:10])
        more = f" +{len(syms) - 10} more" if len(syms) > 10 else ""
        log.warning(
            "NO BARS for %d symbol(s) on %s @ %s — all %d sources empty (%s%s)",
            len(syms), day, interval, _EMPTY_SOURCES.get((day, interval), 0),
            shown, more)
    _EMPTY.clear()
    _EMPTY_SOURCES.clear()


import atexit as _atexit
_atexit.register(flush_empty_summary)
