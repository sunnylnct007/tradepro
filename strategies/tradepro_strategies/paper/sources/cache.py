"""ParquetBarStore + CachedSource — local disk cache for intraday bars.

Why this exists:
  - Every backtest run re-fetches the same bars from Yahoo (60s
    granularity, ~50 symbols × 13 universes). That's both slow and
    rate-limit-prone — the Mac worker already hits Yahoo enough to
    occasionally trip throttling. Cache hits skip the network entirely.
  - Yahoo's 1m intraday window is 60 days. Anything older is gone
    forever from that endpoint. Persisting fetched sessions to disk
    means an October 2025 session captured today can be replayed in
    May 2026 long after Yahoo has dropped it from its window.

Layout on disk (one parquet per (symbol, interval, session_date)):
    ~/.tradepro/cache/intraday/<symbol>/<interval>/<YYYY-MM-DD>.parquet

Schema (one row per Bar):
    timestamp: timestamp[ns, UTC]
    open / high / low / close: float64
    volume: int64

`symbol` and `timeframe_seconds` are not stored in the row schema —
they're encoded in the directory + filename + parquet path metadata
because they're constant for the whole file (saves space + makes
DuckDB-over-the-cache trivially indexable).

Freshness: today's session is volatile (bars still landing); the
cache writer refuses to persist a fetch whose `session_date` is
>= today UTC unless `cache_today=True` is set. Reader is more
forgiving — if there's a file for today's date the reader returns
it (operator's job to invalidate when the live session ends).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..strategy import Bar
from .base import BarSource


log = logging.getLogger("tradepro.paper.cache")


@dataclass
class ParquetBarStore:
    """Read/write helpers for the intraday bar cache. No vendor
    coupling — just bars in and bars out."""

    root: Path = field(
        default_factory=lambda: Path.home() / ".tradepro" / "cache" / "intraday"
    )

    def path_for(
        self, symbol: str, session_date: datetime, interval: str
    ) -> Path:
        date_str = session_date.date().isoformat()
        return self.root / symbol / interval / f"{date_str}.parquet"

    def exists(
        self, symbol: str, session_date: datetime, interval: str
    ) -> bool:
        return self.path_for(symbol, session_date, interval).exists()

    def read(
        self, symbol: str, session_date: datetime, interval: str
    ) -> list[Bar]:
        """Pull bars out of disk. Returns [] when the file is missing
        rather than raising — keeps the fallback chain simple."""
        path = self.path_for(symbol, session_date, interval)
        if not path.exists():
            return []
        try:
            import pyarrow.parquet as pq
        except ImportError:
            log.warning("pyarrow not installed; bar cache disabled")
            return []
        table = pq.read_table(path)
        meta = table.schema.metadata or {}
        timeframe_seconds = int(
            meta.get(b"timeframe_seconds", str(_interval_to_seconds(interval)).encode())
        )
        timestamps = table.column("timestamp").to_pylist()
        opens = table.column("open").to_pylist()
        highs = table.column("high").to_pylist()
        lows = table.column("low").to_pylist()
        closes = table.column("close").to_pylist()
        volumes = table.column("volume").to_pylist()
        bars: list[Bar] = []
        for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(Bar(
                symbol=symbol, timestamp=ts,
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=int(v), timeframe_seconds=timeframe_seconds,
            ))
        return bars

    def write(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
        bars: list[Bar],
    ) -> None:
        """Persist a session's bars. No-op on empty bars list — we
        don't want to plant a 'cached: empty' file that would mask
        the next source in the fallback chain from ever being tried."""
        if not bars:
            return
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            log.warning("pyarrow not installed; skipping cache write")
            return
        path = self.path_for(symbol, session_date, interval)
        path.parent.mkdir(parents=True, exist_ok=True)
        timeframe_seconds = bars[0].timeframe_seconds
        table = pa.table(
            {
                "timestamp": [b.timestamp for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
                "volume": [b.volume for b in bars],
            },
            metadata={
                b"symbol": symbol.encode(),
                b"interval": interval.encode(),
                b"timeframe_seconds": str(timeframe_seconds).encode(),
                b"written_at_utc": datetime.now(timezone.utc).isoformat().encode(),
            },
        )
        # Atomic-ish write: stage to a sibling file then rename, so
        # a partial write doesn't leave a corrupt parquet behind to
        # poison the cache on next read.
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(table, tmp)
        os.replace(tmp, path)


@dataclass
class CachedSource(BarSource):
    """Wrap any `BarSource` with a `ParquetBarStore` read-through cache.

    Read path: cache hit → return cached bars. Miss → delegate to
    `inner`, persist its result, return.

    Today's-session policy: by default the cache REFUSES to write a
    file for `session_date >= today UTC` because intraday data is
    still landing (caching mid-session would freeze a partial day on
    disk forever). The reader will still serve today's file if the
    operator has explicitly cached it, so backfills and replays of
    finished today-sessions both work.
    """

    inner: BarSource = None  # type: ignore[assignment]
    store: ParquetBarStore = field(default_factory=ParquetBarStore)
    cache_today: bool = False
    name: str = "cached_source"

    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        cached = self.store.read(symbol, session_date, interval)
        if cached:
            log.debug(
                "cache hit %s %s %s (%d bars)",
                symbol, session_date.date(), interval, len(cached),
            )
            return cached
        if self.inner is None:
            return []
        bars = await self.inner.fetch(symbol, session_date, interval)
        if bars and self._should_persist(session_date):
            self.store.write(symbol, session_date, interval, bars)
            log.info(
                "cache miss %s %s %s → fetched %d bars from %s, persisted",
                symbol, session_date.date(), interval,
                len(bars), getattr(self.inner, "name", "inner"),
            )
        return bars

    def _should_persist(self, session_date: datetime) -> bool:
        if self.cache_today:
            return True
        today = datetime.now(timezone.utc).date()
        return session_date.date() < today


def _interval_to_seconds(interval: str) -> int:
    table = {
        "1m": 60, "2m": 120, "5m": 300, "15m": 900,
        "30m": 1800, "60m": 3600, "1h": 3600, "1d": 86_400,
    }
    return table.get(interval, 60)


__all__ = ["ParquetBarStore", "CachedSource"]
