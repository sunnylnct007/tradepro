"""Data loaders. Mirrors the backend's provider interface so local research and
server runs agree on the same shape."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from typing import Literal

import pandas as pd
import requests

Provider = Literal["yahoo", "stooq", "binance"]


@dataclass
class DataRequest:
    symbol: str
    start: datetime
    end: datetime
    interval: str = "1d"
    provider: Provider = "yahoo"


def load_candles(req: DataRequest) -> pd.DataFrame:
    """Return a DataFrame indexed by timestamp with columns
    [open, high, low, close, adj_close, volume]."""
    if req.provider == "yahoo":
        return _yahoo(req)
    if req.provider == "stooq":
        return _stooq(req)
    if req.provider == "binance":
        return _binance(req)
    raise ValueError(f"unknown provider: {req.provider}")


def _yahoo(req: DataRequest) -> pd.DataFrame:
    # yfinance is imported lazily so the package still imports without it.
    import yfinance as yf

    def _dl(sym: str) -> pd.DataFrame:
        return yf.download(
            sym,
            start=req.start.strftime("%Y-%m-%d"),
            end=req.end.strftime("%Y-%m-%d"),
            interval=req.interval,
            auto_adjust=False,
            progress=False,
        )

    df = _dl(req.symbol)
    # LSE auto-suffix (Bug #17): if a bare ticker comes back empty and
    # looks like an LSE UCITS ETF / equity (3-4 caps, no dot, no caret),
    # retry once with ".L" appended — VWRL → VWRL.L. Cheap fallback that
    # rescues users typing the un-suffixed form they see on brokerage UIs.
    if df.empty and _looks_like_lse(req.symbol):
        df = _dl(f"{req.symbol}.L")
    if df.empty:
        return df
    # yfinance >=0.2.40 returns MultiIndex columns even for a single ticker;
    # drop the ticker level so we get plain column names.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel("Ticker")
    df = df.rename(
        columns={"Open": "open", "High": "high", "Low": "low",
                 "Close": "close", "Adj Close": "adj_close", "Volume": "volume"}
    )
    df.index.name = "timestamp"
    return df[["open", "high", "low", "close", "adj_close", "volume"]]


def _looks_like_lse(sym: str) -> bool:
    if not sym or "." in sym or sym.startswith("^") or "=" in sym:
        return False
    s = sym.upper()
    if not (3 <= len(s) <= 5):
        return False
    return all(c.isalpha() for c in s)


def _stooq(req: DataRequest) -> pd.DataFrame:
    sym = req.symbol.lower()
    if "." not in sym:
        sym = f"{sym}.us"
    url = (
        "https://stooq.com/q/d/l/?"
        f"s={sym}&d1={req.start:%Y%m%d}&d2={req.end:%Y%m%d}&i=d"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    text = resp.text
    if not text or text.startswith("No data"):
        return pd.DataFrame()
    df = pd.read_csv(StringIO(text))
    df["Date"] = pd.to_datetime(df["Date"], utc=True)
    df = df.set_index("Date").rename(columns=str.lower)
    df["adj_close"] = df["close"]
    df.index.name = "timestamp"
    return df[["open", "high", "low", "close", "adj_close", "volume"]]


def _binance(req: DataRequest) -> pd.DataFrame:
    start_ms = int(req.start.timestamp() * 1000)
    end_ms = int(req.end.timestamp() * 1000)
    rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        url = (
            "https://api.binance.com/api/v3/klines"
            f"?symbol={req.symbol.upper()}&interval={req.interval}"
            f"&startTime={cursor}&endTime={end_ms}&limit=1000"
        )
        data = requests.get(url, timeout=15).json()
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cursor = data[-1][0] + 1
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=[
        "open_ms", "open", "high", "low", "close", "volume",
        "close_ms", "quote_vol", "trades", "tbbav", "tbqav", "ignore",
    ])
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["timestamp"] = pd.to_datetime(df["open_ms"], unit="ms", utc=True)
    df = df.set_index("timestamp")
    df["adj_close"] = df["close"]
    return df[["open", "high", "low", "close", "adj_close", "volume"]]
