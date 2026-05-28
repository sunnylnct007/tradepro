"""Local Parquet cache of OHLCV candles.

Layout (under ~/.tradepro/cache):
    cache/<provider>/<interval>/<safe_symbol>.parquet
    cache/<provider>/<interval>/<safe_symbol>.meta.json

The Parquet file holds the bars. The sidecar JSON records provenance:
    - provider, symbol, interval
    - first / last bar timestamp
    - fetched_at (UTC)
    - row_count

Idempotent: refresh merges with any existing rows by timestamp, so you can
re-run the same window without losing history.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import DataRequest, load_candles

CACHE_ROOT = Path.home() / ".tradepro" / "cache"


def _safe(symbol: str) -> str:
    return symbol.replace("/", "_").replace("^", "_idx_").replace(":", "_")


def _to_utc(x) -> pd.Timestamp:
    """Coerce anything timestamp-shaped into a tz-aware UTC pandas Timestamp.
    Stooq returns tz-aware UTC, yfinance returns tz-naive — without this
    helper, comparisons in `ensure_cached` raise TypeError."""
    ts = pd.Timestamp(x)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def paths(provider: str, symbol: str, interval: str) -> tuple[Path, Path]:
    base = CACHE_ROOT / provider / interval / _safe(symbol)
    return base.with_suffix(".parquet"), base.with_suffix(".meta.json")


@dataclass
class CacheMeta:
    provider: str
    symbol: str
    interval: str
    first_ts: str | None
    last_ts: str | None
    row_count: int
    fetched_at: str


def load_cached(provider: str, symbol: str, interval: str = "1d") -> pd.DataFrame:
    p, _ = paths(provider, symbol, interval)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_parquet(p)


def load_meta(provider: str, symbol: str, interval: str = "1d") -> CacheMeta | None:
    _, mp = paths(provider, symbol, interval)
    if not mp.exists():
        return None
    return CacheMeta(**json.loads(mp.read_text()))


def refresh_symbol(
    provider: str,
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
) -> int:
    """Fetch [start, end] for this symbol and merge into the cache.
    Returns the total number of cached bars after the merge."""
    p, mp = paths(provider, symbol, interval)
    p.parent.mkdir(parents=True, exist_ok=True)

    fresh = load_candles(DataRequest(
        symbol=symbol, start=start, end=end, interval=interval, provider=provider,
    ))
    if fresh.empty:
        # Nothing new — leave the existing cache untouched.
        existing = load_cached(provider, symbol, interval)
        return len(existing)

    existing = load_cached(provider, symbol, interval)
    if existing.empty:
        merged = fresh
    else:
        merged = pd.concat([existing, fresh])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()

    merged.to_parquet(p)

    meta = CacheMeta(
        provider=provider,
        symbol=symbol,
        interval=interval,
        first_ts=str(merged.index[0]) if not merged.empty else None,
        last_ts=str(merged.index[-1]) if not merged.empty else None,
        row_count=len(merged),
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )
    mp.write_text(json.dumps(meta.__dict__, indent=2, default=str))
    return len(merged)


def ensure_cached(
    provider: str,
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
) -> pd.DataFrame:
    """Return data in [start, end] from cache, fetching if missing."""
    df = load_cached(provider, symbol, interval)
    meta = load_meta(provider, symbol, interval)
    need_refresh = (
        df.empty
        or meta is None
        or _to_utc(meta.first_ts) > _to_utc(start)
        or _to_utc(meta.last_ts) < _to_utc(end) - pd.Timedelta(days=7)
    )
    if need_refresh:
        refresh_symbol(provider, symbol, start, end, interval)
        df = load_cached(provider, symbol, interval)
    if df.empty:
        return df
    start_ts = _to_utc(start)
    end_ts = _to_utc(end)
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
        df = df.copy()
        df.index = idx
    return df[(df.index >= start_ts) & (df.index <= end_ts)]
