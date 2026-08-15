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
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .data import DataRequest, load_candles

# A dropped bar this recent means the FEED is failing now (actionable);
# anything older is a known historical corruption we silently re-drop.
_GARBAGE_RECENT_DAYS = 5

_log = logging.getLogger("tradepro.cache")

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


def _drop_garbage_bars(df: pd.DataFrame, *, symbol: str | None = None, provider: str | None = None) -> pd.DataFrame:
    """Drop isolated garbage bars before caching.

    Providers (Yahoo) occasionally emit phantom bars on US market HOLIDAYS — an
    absurd price (~10-15x real) with tiny volume (e.g. VLUE 2026-02-16 close
    2313, vol 217; 2026-01-19 close 2239, vol 19). Cached unchecked, these
    become the 52w high → corrupt range/drawdown → a FALSE dip-buy BUY, and they
    poison backtests + charts too. A bar whose close is >4x or <1/4x BOTH
    neighbours is not a real price for a listed instrument (real splits are
    handled via adj_close) — drop the whole row. Only ISOLATED spikes are
    removed, so genuine rallies/gaps are untouched.

    Also drops any row with a NaN close — a partial-fetch bar (seen on SPY
    2026-08-03: open/high/low/volume populated, close missing) that silently
    poisons every downstream close-comparison to False forever (NaN
    comparisons are always False in Python), e.g. the regime-filter gate in
    ichimoku_equity reading "not green" market-wide with no error raised.
    """
    if df is None or len(df) < 1 or "close" not in df.columns:
        return df
    c = df["close"]
    nan_bad = c.isna()
    bad = nan_bad
    if len(df) >= 3:
        prev, nxt = c.shift(1), c.shift(-1)
        spike = (((c > prev * 4) & (c > nxt * 4)) | ((c < prev * 0.25) & (c < nxt * 0.25))).fillna(False)
        bad = bad | spike
    if bool(bad.any()):
        import datetime as _d
        n_nan, n_spike = int(nan_bad.sum()), int((bad & ~nan_bad).sum())
        bad_idx = list(df.index[bad])
        dates = [str(d)[:10] for d in bad_idx]
        # ── Noise control + unambiguous dating (owner, 15 Aug 2026: "too many
        # bar errors ... not even clear if it's today or yesterday").
        #
        # Two different things were being logged at the same volume and
        # urgency: a bar from YESTERDAY that should exist (actionable — the
        # feed is failing NOW) and a corrupt bar from 2020 that we re-drop
        # and re-announce on every single run forever (pure noise; CL=F
        # 2020-04-20 and ADM 2024 were reported daily for months).
        #
        # Only RECENT drops raise a run_log warn, and the message states the
        # run date and the bar's age in days so "today or yesterday" is never
        # a question. Historical drops stay in the local log and are counted
        # in the same line, never alarmed individually.
        today = _d.date.today()
        def _age_days(x) -> int | None:
            try:
                return (today - _d.date.fromisoformat(str(x)[:10])).days
            except ValueError:
                return None
        ages = {d: _age_days(d) for d in dates}
        recent = [d for d, a in ages.items() if a is not None and a <= _GARBAGE_RECENT_DAYS]
        historical = [d for d in dates if d not in recent]
        detail = (f"dropped {int(bad.sum())} garbage bar(s) for {symbol or '?'} "
                  f"({n_nan} NaN-close, {n_spike} price-spike): {dates}")
        _log.warning(detail)
        if recent:
            when = ", ".join(
                f"{d} ({'today' if ages[d] == 0 else 'yesterday' if ages[d] == 1 else f'{ages[d]}d ago'})"
                for d in sorted(recent))
            summary = (f"{symbol or '?'}: dropped {len(recent)} RECENT garbage bar(s) "
                       f"[{when}] on a run dated {today.isoformat()} "
                       f"({n_nan} NaN-close, {n_spike} price-spike)"
                       + (f"; plus {len(historical)} historical bar(s) re-dropped, not alarmed"
                          if historical else ""))
            try:
                from .run_log import log_run
                log_run("bar-cache", "garbage-bar-drop", "warn",
                         broker=provider, symbol=symbol, error=summary)
            except Exception:  # noqa: BLE001 — observability must never break the fetch
                _log.debug("garbage-bar-drop run_log post failed (non-fatal)", exc_info=True)
        df = df.loc[~bad]
    return df


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

    # Strip provider garbage (holiday/glitch spike bars) so the cache stays
    # clean for every consumer (signals, backtests, charts). Self-heals any
    # already-cached bad bars on the next refresh.
    merged = _drop_garbage_bars(merged, symbol=symbol, provider=provider)

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
