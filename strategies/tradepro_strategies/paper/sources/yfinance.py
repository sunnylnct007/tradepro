"""YfinanceSource — pulls one intraday session from Yahoo Finance.

Wraps the same fetch logic that `YfinanceIntradayBus` uses but exposes
it as a `BarSource` so it can be composed under CachedSource +
FallbackSource. The existing `YfinanceIntradayBus` is unchanged for
back-compat; new code should prefer
`SourceBackedBus(CachedSource(YfinanceSource()))`.

Yahoo's intraday windows (observed against the live endpoint):
  1m:        last ~30 days
  5m / 15m:  last ~60 days
  60m:       last ~730 days
Older calls return empty with a "delisted / no price data" error —
`FallbackSource` will then walk to the next configured source
(Finnhub serves intraday well beyond Yahoo's 30-day 1m window).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ...yf_noise import quiet_yfinance_delisted_noise
from ..strategy import Bar
from .base import BarSource


log = logging.getLogger("tradepro.paper.sources.yfinance")

# Silence yfinance's per-symbol-per-day "possibly delisted; no price data"
# ERROR spam (market-holiday gaps trip it for the whole universe). Our own
# log lines below carry the meaningful "NO BARS after retries" signal.
quiet_yfinance_delisted_noise()


# Canonical pair → Yahoo ticker. Internal code uses the canonical
# name (e.g. Bar.symbol, strategy.pairs); only the fetch boundary
# translates. Note USDJPY/USDCHF/USDCAD use Yahoo's terse JPY=X etc.
_FX_YAHOO_TICKER: dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCHF": "CHF=X",
    "USDCAD": "CAD=X",
    "NZDUSD": "NZDUSD=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}


def _yahoo_ticker(symbol: str) -> str:
    """Translate a canonical symbol to the ticker Yahoo expects.

    Pass-through for symbols that already carry a Yahoo suffix
    (`EURUSD=X`, `BTC-USD`) or for regular equities (`AAPL`).
    """
    if "=" in symbol or symbol.endswith("-USD"):
        return symbol
    return _FX_YAHOO_TICKER.get(symbol.upper(), symbol)


@dataclass
class YfinanceSource(BarSource):
    """One-call Yahoo fetcher. Stateless — safe to share across
    multiple symbols / sessions."""

    name: str = "yfinance"

    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        return await asyncio.to_thread(self._fetch_sync, symbol, session_date, interval)

    async def fetch_range(
        self,
        symbol: str,
        start_date: datetime,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        """Bulk window fetch — ONE ranged download covering
        [start_date .. session_date] instead of N per-day calls.

        The per-day warmup fan-out (`_fetch_window` → `fetch` per business
        day) rate-limits badly for a multi-symbol, deep-history strategy:
        FX needs ~800 hourly bars (its Ichimoku horizons run to 624h), and
        fetching that day-by-day for 10 pairs is ~hundreds of Yahoo calls →
        429s → dropped pairs / starved signals. One ranged call per symbol
        fixes it (10 calls total for the whole FX universe)."""
        return await asyncio.to_thread(
            self._fetch_range_sync, symbol, start_date, session_date, interval)

    @staticmethod
    def _download_with_retry(yahoo_ticker, start_iso, end_iso, interval, symbol):
        """yf.download with 3-attempt backoff (Yahoo 429s → empty df).
        Returns the df (possibly None/empty after retries)."""
        import time as _time
        import yfinance as yf
        df = None
        for attempt in range(3):
            try:
                df = yf.download(
                    yahoo_ticker, start=start_iso, end=end_iso,
                    interval=interval, auto_adjust=False, progress=False,
                )
            except Exception as exc:  # noqa: BLE001 — transient yfinance/HTTP error
                log.warning("yfinance: fetch error for %s (%s) attempt %d/3: %s",
                            symbol, yahoo_ticker, attempt + 1, exc)
                df = None
            if df is not None and not df.empty:
                return df
            if attempt < 2:
                _time.sleep(1.5 * (attempt + 1))  # 1.5s, 3.0s backoff
        return df

    @staticmethod
    def _df_to_bars(df, symbol: str, interval: str) -> list[Bar]:
        import pandas as pd
        if df is None or df.empty:
            return []
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel("Ticker")
        tf_seconds = _interval_seconds(interval)
        bars: list[Bar] = []
        for ts, row in df.iterrows():
            ts_utc = (
                ts.tz_convert("UTC")
                if hasattr(ts, "tz_convert") and ts.tzinfo is not None
                else ts.tz_localize("America/New_York").tz_convert("UTC")
                if hasattr(ts, "tz_localize")
                else ts
            )
            bars.append(Bar(
                # Preserve the canonical symbol so downstream filters
                # (strategy.pairs, ledger keys) still match.
                symbol=symbol,
                timestamp=ts_utc.to_pydatetime() if hasattr(ts_utc, "to_pydatetime") else ts_utc,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                timeframe_seconds=tf_seconds,
            ))
        return bars

    @staticmethod
    def _fetch_sync(symbol: str, session_date: datetime, interval: str) -> list[Bar]:
        start = session_date.date().isoformat()
        end_dt = session_date.date() + timedelta(days=1)
        yahoo_ticker = _yahoo_ticker(symbol)
        df = YfinanceSource._download_with_retry(
            yahoo_ticker, start, end_dt.isoformat(), interval, symbol)
        if df is None or df.empty:
            # DEBUG, not WARNING: the fallback chain reports the decisive
            # outcome ("no source served this at all") as one counted summary
            # per run. This line fired 1,106 times in a single run on 3 Sep,
            # almost all of it "the US market has not opened yet" — noise that
            # buried a real strategy-starvation finding. See
            # paper/sources/fallback.py:_record_empty.
            log.debug("yfinance: NO BARS after retries for %s (%s) %s–%s @ %s "
                      "— falling through the chain",
                      symbol, yahoo_ticker, start, end_dt.isoformat(), interval)
            return []
        return YfinanceSource._df_to_bars(df, symbol, interval)

    @staticmethod
    def _fetch_range_sync(symbol: str, start_date: datetime,
                          session_date: datetime, interval: str) -> list[Bar]:
        start = start_date.date().isoformat()
        end_dt = session_date.date() + timedelta(days=1)
        yahoo_ticker = _yahoo_ticker(symbol)
        df = YfinanceSource._download_with_retry(
            yahoo_ticker, start, end_dt.isoformat(), interval, symbol)
        if df is None or df.empty:
            log.warning("yfinance: NO BARS (range) for %s (%s) %s–%s @ %s",
                        symbol, yahoo_ticker, start, end_dt.isoformat(), interval)
            return []
        bars = YfinanceSource._df_to_bars(df, symbol, interval)
        log.info("yfinance: range %s (%s) %s–%s @ %s → %d bars",
                 symbol, yahoo_ticker, start, end_dt.isoformat(), interval, len(bars))
        return bars


def _interval_seconds(s: str) -> int:
    table = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600}
    return table.get(s, 60)


__all__ = ["YfinanceSource"]
