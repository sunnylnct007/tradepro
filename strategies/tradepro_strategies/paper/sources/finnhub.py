"""FinnhubSource — pulls one intraday session from Finnhub's
/stock/candle endpoint.

Finnhub free tier: 60 calls/min, 30 calls/sec, and `/stock/candle` is
allowed for US stocks. Resolutions: 1, 5, 15, 30, 60 minutes (string
form: "1", "5", "15", "30", "60", "D", "W", "M"). Returns an array-of-
arrays JSON {t,o,h,l,c,v,s}.

History depth: Finnhub serves intraday well beyond Yahoo's 60-day
window, which is the main reason this source exists in the fallback
chain — older replay sessions land here when Yahoo returns empty.

Credentials: reads `TRADEPRO_FINNHUB_API_KEY` (matches the env var
the .NET backend already uses) or accepts an explicit `api_key` arg
for tests.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from ...secrets import get_secret
from ..strategy import Bar
from .base import BarSource


log = logging.getLogger("tradepro.paper.sources.finnhub")


# Yahoo-style → Finnhub resolution strings. Finnhub uses bare integers
# (in minutes) for intraday; daily uses "D". Anything outside this
# table falls back to "1" so the call still succeeds rather than 4xx.
_INTERVAL_TO_RESOLUTION = {
    "1m": "1", "5m": "5", "15m": "15", "30m": "30", "60m": "60", "1h": "60",
}
_INTERVAL_TO_SECONDS = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600,
}


@dataclass
class FinnhubSource(BarSource):
    """Stateless Finnhub /stock/candle adapter. Pair with `CachedSource`
    to keep your free-tier budget intact across re-runs of the same
    session."""

    api_key: Optional[str] = None
    base_url: str = "https://finnhub.io/api/v1"
    timeout_seconds: float = 10.0
    name: str = "finnhub"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = get_secret("finnhub-api-key")

    async def fetch(
        self,
        symbol: str,
        session_date: datetime,
        interval: str,
    ) -> list[Bar]:
        if not self.api_key:
            log.warning(
                "FinnhubSource has no API key (TRADEPRO_FINNHUB_API_KEY); returning []"
            )
            return []
        resolution = _INTERVAL_TO_RESOLUTION.get(interval, "1")
        tf_seconds = _INTERVAL_TO_SECONDS.get(interval, 60)
        # Finnhub takes UNIX timestamps. Bound the request to the
        # regular US session 09:30 → 16:00 ET → 14:30 → 21:00 UTC
        # (close enough for non-DST math; the response is filtered
        # by Finnhub to what actually traded). Adding 1h slack to
        # absorb DST transitions without hand-rolling pytz.
        local_date = session_date.date()
        start_utc = datetime.combine(local_date, time(13, 0), tzinfo=timezone.utc)
        end_utc = datetime.combine(local_date, time(22, 0), tzinfo=timezone.utc)
        params = {
            "symbol": symbol,
            "resolution": resolution,
            "from": int(start_utc.timestamp()),
            "to": int(end_utc.timestamp()),
            "token": self.api_key,
        }
        try:
            import httpx
        except ImportError:
            log.warning("httpx not installed; FinnhubSource disabled")
            return []
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(f"{self.base_url}/stock/candle", params=params)
                resp.raise_for_status()
                payload = resp.json()
        except Exception:
            log.exception("Finnhub candle fetch failed for %s %s", symbol, local_date)
            return []
        # `s` is the status field: "ok" / "no_data". `t/o/h/l/c/v` are
        # parallel arrays.
        if payload.get("s") != "ok":
            return []
        ts_arr = payload.get("t") or []
        if not ts_arr:
            return []
        bars: list[Bar] = []
        for ts, o, h, l, c, v in zip(
            ts_arr, payload["o"], payload["h"], payload["l"], payload["c"], payload["v"],
        ):
            bars.append(Bar(
                symbol=symbol,
                timestamp=datetime.fromtimestamp(int(ts), tz=timezone.utc),
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=int(v), timeframe_seconds=tf_seconds,
            ))
        return bars


__all__ = ["FinnhubSource"]
