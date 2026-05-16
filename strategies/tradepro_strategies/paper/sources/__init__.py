"""BarSource — pluggable fetch-one-session abstraction for the paper engine.

A `BarSource` answers ONE question: give me the bars for (symbol,
session_date, interval). Independent of the `BarBus`, which answers
the orthogonal question: how should bars stream onto an asyncio queue.

This split lets the orchestration patterns we care about compose by
wrapping:

  CachedSource(YfinanceSource())                        # parquet on disk
  FallbackSource([CachedSource(YfinanceSource()),       # try cache + Yahoo
                  CachedSource(FinnhubSource())])       #    fall back to Finnhub
  SourceBackedBus(<any source>, pace_seconds=...)       # adapt to BarBus

The motivation:
  - Yahoo's 1m endpoint window is 60 days; any older session can't
    be backtested through it. A cache makes already-fetched sessions
    forever-available; a fallback lets Finnhub (or future Polygon/
    Alpaca) fill the gap.
  - Every backtest run today re-fetches the same bars from Yahoo;
    `CachedSource` is the obvious win.
  - When one source rate-limits / 5xxs, the engine should silently
    swap to the next without aborting the session. `FallbackSource`
    captures that.

Microservices migration: each source becomes a tiny HTTP/MCP service
behind the same `fetch(symbol, date, interval) → list[Bar]` contract.
The Engine's BarBus stays in-process; only the source layer crosses
the network.
"""
from .base import BarSource, SourceBackedBus
from .cache import CachedSource, ParquetBarStore
from .fallback import FallbackSource
from .finnhub import FinnhubSource
from .yfinance import YfinanceSource

__all__ = [
    "BarSource",
    "SourceBackedBus",
    "CachedSource",
    "ParquetBarStore",
    "FallbackSource",
    "FinnhubSource",
    "YfinanceSource",
]
