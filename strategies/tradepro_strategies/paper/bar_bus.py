"""BarBus — publishes `BarEvent`s onto an output queue.

Service boundary: BarBus is the ONLY thing that decides "what bars
exist for which symbols". Strategies subscribe to a symbol via the
engine; the bus emits one BarEvent per bar to the output queue and
the engine fans out to subscribed strategies.

Two concrete implementations today:

  ReplayBarBus    — pulls bars from a list (or async generator) and
                    emits them in timestamp order with optional
                    real-time pacing. Use for backtest / replay
                    sessions / unit tests.
  YfinanceIntradayBus
                  — fetches an intraday-bar history from Yahoo
                    Finance and replays it. Cheap way to drive ORB
                    against a real day without an IBKR account. Bars
                    are pulled once at session_start and then
                    replayed; not a live stream.

The LiveIBKRBarBus that wraps `ib_insync` lives in a future commit;
the protocol below is what it'll implement. Keeping the protocol +
two replay impls in this file is enough to drive the engine today.

When this service moves to its own process (microservice split):
  - Subscribe / publish use Redis Streams instead of asyncio.Queue
  - Same BarEvent dataclass; `to_wire(event)` adapts cleanly
  - Bus health surfaces via `HeartbeatEvent` on the heartbeat stream
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import AsyncIterator, Iterable

from .messages import BarEvent, ShutdownEvent
from .strategy import Bar

_log = logging.getLogger(__name__)

# Isolated intraday spike guard threshold. Yahoo's 1-minute feed occasionally
# emits a phantom tick — a single bar wildly off both neighbours (the UNH ~$377
# phantom that fired a false stop, memory: project_data_quality_findings). Unlike
# the DAILY holiday phantoms (10-15x, caught by cache._drop_garbage_bars' 4x rule)
# intraday phantoms are smaller (~10-30%), so we use a PERCENTAGE isolated-spike
# test. Genuine >12% one-minute moves-then-full-reversion are vanishingly rare
# (LULD halts fire first), so an isolated 12% round-trip is a bad tick, not a move.
_INTRADAY_SPIKE_PCT = 0.12


def sanitize_intraday_bars(
    bars: list[Bar], spike_pct: float = _INTRADAY_SPIKE_PCT
) -> list[Bar]:
    """Drop ISOLATED spike bars from a live intraday sequence.

    A bar is an isolated spike when its close deviates > spike_pct from BOTH
    neighbours in the SAME direction while the two neighbours agree with each
    other (gap < spike_pct/2) — i.e. a lone bad tick that reverts, not a real
    trend leg or gap. Golden-source note: when an IBKR feed is available its
    ticks are authoritative and this bronze-feed guard is unnecessary; it exists
    to protect the yfinance intraday path (see [[reuse_ibkr]] roadmap).
    """
    if len(bars) < 3:
        return bars
    out = [bars[0]]
    dropped: list[str] = []
    for i in range(1, len(bars) - 1):
        prev, cur, nxt = bars[i - 1].close, bars[i].close, bars[i + 1].close
        if prev <= 0 or cur <= 0 or nxt <= 0:
            out.append(bars[i])
            continue
        dev_prev = cur / prev - 1.0
        dev_nxt = cur / nxt - 1.0
        neighbour_gap = abs(nxt / prev - 1.0)
        isolated = (
            abs(dev_prev) > spike_pct
            and abs(dev_nxt) > spike_pct
            and (dev_prev > 0) == (dev_nxt > 0)   # spikes the same way vs both sides
            and neighbour_gap < spike_pct / 2     # neighbours agree → the spike is lone
        )
        if isolated:
            dropped.append(f"{bars[i].timestamp:%H:%M}={cur:.2f}")
            continue
        out.append(bars[i])
    out.append(bars[-1])
    if dropped:
        _log.warning(
            "sanitize_intraday_bars[%s]: dropped %d isolated spike bar(s): %s",
            bars[0].symbol, len(dropped), ", ".join(dropped),
        )
    return out


class BarBus(ABC):
    """Common interface every bar source implements.

    The bus is one-publisher, many-subscribers. `run()` is the
    async coroutine the engine awaits — it should pump bars onto
    `out_queue` until exhausted or until a ShutdownEvent appears
    on `shutdown_queue`. Always emits a final ShutdownEvent on
    out_queue so downstream consumers can drain.
    """

    name: str = "bus"

    @abstractmethod
    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        """Pump bars to out_queue until done or shutdown received."""
        ...


@dataclass
class ReplayBarBus(BarBus):
    """Replay a pre-loaded list of bars. Useful for unit tests,
    deterministic backtests, and replaying historical sessions
    captured on disk.

    Bars MUST be sorted by timestamp. If `pace_seconds` is None
    (default) bars are emitted as fast as the consumer can drain;
    set to e.g. 0.05 to simulate a 50ms-per-bar feed for UI testing.
    Set to 'realtime' to pace to the bars' own timestamps (use
    when demonstrating live behaviour off historical data).
    """

    bars: Iterable[Bar]
    pace_seconds: float | str | None = None
    name: str = "replay_bus"

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        from dataclasses import replace as _dc_replace
        # Materialise so we can find the LAST bar per symbol → the "live" bar.
        # Net-position strategies use the rest as indicator warmup and act
        # once, on the live bar, instead of re-trading every replayed bar.
        bars = list(self.bars)
        last_index_per_symbol: dict[str, int] = {}
        for i, b in enumerate(bars):
            last_index_per_symbol[b.symbol] = i
        live_indices = set(last_index_per_symbol.values())
        seq = 0
        prev_ts: datetime | None = None
        for i, bar in enumerate(bars):
            if not shutdown_queue.empty():
                break
            if self.pace_seconds == "realtime" and prev_ts is not None:
                gap = (bar.timestamp - prev_ts).total_seconds()
                if gap > 0:
                    await asyncio.sleep(gap)
            elif isinstance(self.pace_seconds, (int, float)) and self.pace_seconds > 0:
                await asyncio.sleep(self.pace_seconds)
            if i in live_indices and not bar.is_live:
                bar = _dc_replace(bar, is_live=True)
            await out_queue.put(BarEvent(bar=bar, sequence=seq))
            seq += 1
            prev_ts = bar.timestamp
        # Signal end-of-stream so the engine can drain.
        await out_queue.put(ShutdownEvent(reason=f"{self.name} exhausted"))


@dataclass
class YfinanceIntradayBus(BarBus):
    """Pull one intraday session of bars from Yahoo Finance for the
    given symbol + date and replay them.

    Yahoo's intraday endpoints have a 60-day window for 1-minute
    data, ~730d for 5m / 15m. Beyond that the request returns empty
    — strategies relying on older replay must source bars via
    another path (e.g., a TimescaleDB cache fed by IBKR historical).

    `session_date` is the local exchange date (America/New_York
    for US symbols). Returns the trading window 09:30 → 16:00 ET.
    """

    symbol: str
    session_date: datetime          # interpret as the LOCAL exchange date
    interval: str = "1m"            # yfinance interval: 1m / 5m / 15m / 1h
    pace_seconds: float | str | None = None

    @property
    def name(self) -> str:
        return f"yfinance:{self.symbol}:{self.session_date.date().isoformat()}"

    async def run(
        self,
        out_queue: asyncio.Queue,
        shutdown_queue: asyncio.Queue,
    ) -> None:
        bars = await asyncio.to_thread(self._fetch)
        replay = ReplayBarBus(bars=bars, pace_seconds=self.pace_seconds, name=self.name)
        await replay.run(out_queue, shutdown_queue)

    def _fetch(self) -> list[Bar]:
        import yfinance as yf
        import pandas as pd

        # yfinance's intraday endpoint takes start/end as date strings
        # and returns the regular session bars in exchange-local tz.
        start = self.session_date.date().isoformat()
        end_dt = self.session_date.date() + timedelta(days=1)
        df = yf.download(
            self.symbol,
            start=start,
            end=end_dt.isoformat(),
            interval=self.interval,
            auto_adjust=False,
            progress=False,
        )
        if df.empty:
            return []
        # yfinance >=0.2.40 returns MultiIndex columns even for a
        # single ticker; flatten so column lookups stay simple.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel("Ticker")

        bars: list[Bar] = []
        tf_seconds = _interval_seconds(self.interval)
        for ts, row in df.iterrows():
            # Coerce to UTC for storage; engine treats bar timestamps
            # as opaque after this point. Replay/live IBKR/etc all
            # agree on UTC at the bus boundary.
            ts_utc = (
                ts.tz_convert("UTC")
                if hasattr(ts, "tz_convert") and ts.tzinfo is not None
                else ts.tz_localize("America/New_York").tz_convert("UTC")
                if hasattr(ts, "tz_localize")
                else ts
            )
            bars.append(Bar(
                symbol=self.symbol,
                timestamp=ts_utc.to_pydatetime() if hasattr(ts_utc, "to_pydatetime") else ts_utc,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                timeframe_seconds=tf_seconds,
            ))
        # Strip isolated phantom ticks BEFORE the engine sees them, so a bad
        # Yahoo bar can't fire a false stop / bad entry (the UNH ~$377 case).
        return sanitize_intraday_bars(bars)


def _interval_seconds(s: str) -> int:
    """yfinance interval string → seconds. Defaults to 60s if
    unrecognised so callers get a sensible bar object rather than
    a hard fail."""
    table = {
        "1m": 60, "2m": 120, "5m": 300, "15m": 900,
        "30m": 1800, "60m": 3600, "1h": 3600,
    }
    return table.get(s, 60)


# ---- Helper for unit tests / engine smoke ---------------------------

def static_bars(symbol: str, ohlcv_rows: list[tuple]) -> list[Bar]:
    """Construct a list of Bars from compact tuples. Useful for
    deterministic tests of strategies / engine wiring.

    Each row: (timestamp_iso_or_dt, open, high, low, close, volume).
    Frequency defaults to 60s; pass timestamps explicitly to drive
    other timeframes.
    """
    out: list[Bar] = []
    for r in ohlcv_rows:
        ts, o, h, l, c, v = r
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(Bar(
            symbol=symbol, timestamp=ts,
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=int(v), timeframe_seconds=60,
        ))
    return out


__all__ = ["BarBus", "ReplayBarBus", "YfinanceIntradayBus", "static_bars"]
