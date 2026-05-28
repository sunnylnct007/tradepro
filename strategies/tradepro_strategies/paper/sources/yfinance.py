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

from ..strategy import Bar
from .base import BarSource


log = logging.getLogger("tradepro.paper.sources.yfinance")


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

    @staticmethod
    def _fetch_sync(symbol: str, session_date: datetime, interval: str) -> list[Bar]:
        import pandas as pd
        import yfinance as yf

        start = session_date.date().isoformat()
        end_dt = session_date.date() + timedelta(days=1)
        yahoo_ticker = _yahoo_ticker(symbol)
        if yahoo_ticker != symbol:
            log.debug("yfinance: %s → %s", symbol, yahoo_ticker)
        df = yf.download(
            yahoo_ticker, start=start, end=end_dt.isoformat(),
            interval=interval, auto_adjust=False, progress=False,
        )
        if df.empty:
            log.info("yfinance: no bars for %s (%s) %s–%s @ %s",
                     symbol, yahoo_ticker, start, end_dt.isoformat(), interval)
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


def _interval_seconds(s: str) -> int:
    table = {"1m": 60, "2m": 120, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600}
    return table.get(s, 60)


__all__ = ["YfinanceSource"]
