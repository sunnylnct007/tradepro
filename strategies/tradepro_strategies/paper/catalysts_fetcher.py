"""CatalystFetcher — Phase C-3.2.

Reads active catalysts from the .NET ``/api/catalysts/`` registry
on demand and projects them into the ``CatalystHint`` shape the LLM
gate consumes. Built for the paper engine: a single fetcher
instance lives for the strategy's lifetime, caches per-symbol
lookups with a short TTL, and bails to an empty list on any HTTP
hiccup.

Design constraints:

  * **Cheap on the hot path.** Strategies call this per signal — for
    a long-running daemon that's once every few minutes per symbol,
    but a cross-sectional scan hits it many times in a burst. The
    TTL cache means the registry sees one request per (symbol,
    interval) rather than one per evaluate().

  * **Fail-open.** Any HTTP error returns ``[]``. The gate's catalyst
    overlay is purely additive — no catalysts means it falls back
    to the pre-C-3.1 behaviour, exactly the same as if the registry
    were empty.

  * **No background tasks.** No threads, no warmup. Construction is
    free; the first call lazily hits the API. Lets strategies
    inject the fetcher without worrying about lifecycle.

  * **Mirrors the .NET enricher's window.** Default ±30d so the
    Python gate sees the same rows the cockpit (C-3) does.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

import requests

from .llm_gate import CatalystHint

_log = logging.getLogger("tradepro.paper.catalysts_fetcher")


def _parse_occurs_on(value: str | None) -> tuple[str | None, int | None]:
    """Convert the YYYY-MM-DD string the registry returns into
    (occurs_on, days_until). Days-until is computed against today
    (UTC date) — matches the cockpit + .NET enricher conventions."""
    if not value:
        return None, None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return value, None
    today = datetime.now(timezone.utc).date()
    return d.isoformat(), (d - today).days


def _to_hints(rows: Iterable[dict]) -> list[CatalystHint]:
    """Project the registry's JSON shape into CatalystHint records.
    Tolerant of missing keys — the registry surface is small but a
    future field addition shouldn't crash older fetchers."""
    out: list[CatalystHint] = []
    for r in rows or []:
        occurs_on, days_until = _parse_occurs_on(r.get("occurs_on"))
        try:
            cid = int(r.get("id") or 0)
        except (ValueError, TypeError):
            cid = 0
        out.append(CatalystHint(
            id=cid,
            kind=str(r.get("kind") or ""),
            occurs_on=occurs_on,
            severity=str(r.get("severity") or "low"),
            title=str(r.get("title") or ""),
            source=str(r.get("source") or ""),
            days_until=days_until,
        ))
    return out


class CatalystFetcher:
    """HTTP-backed catalyst lookup with per-symbol TTL caching.

    Strategies inject one instance via the ``_catalyst_fetcher`` param
    and call ``fetch(symbol)`` before ``gate.evaluate``. Cached entries
    refresh at most every ``ttl_seconds`` — defaults to 5 minutes so a
    long daemon doesn't hammer the registry but still picks up new
    catalysts from the C-2 cron within a few minutes."""

    def __init__(
        self,
        *,
        api_base: str,
        token: str | None = None,
        lookback_days: int = 30,
        lookahead_days: int = 30,
        ttl_seconds: int = 300,
        timeout: float = 5.0,
        _http_fn: Callable | None = None,
    ) -> None:
        self._api_base = api_base.rstrip("/")
        self._token = token
        self._lookback = max(1, min(int(lookback_days), 365))
        self._lookahead = max(1, min(int(lookahead_days), 365))
        self._ttl = timedelta(seconds=max(0, int(ttl_seconds)))
        self._timeout = float(timeout)
        # Injectable HTTP for tests. Defaults to requests.get.
        self._http_fn = _http_fn or requests.get
        # symbol → (fetched_at, list[CatalystHint])
        self._cache: dict[str, tuple[datetime, list[CatalystHint]]] = {}

    def fetch(self, symbol: str) -> list[CatalystHint]:
        """Return the currently-cached hints for ``symbol``, refreshing
        if the TTL has lapsed. Always returns a list (empty on any
        failure, never raises). Symbol case is normalised to upper.

        Empty / blank symbols return an empty list without hitting
        the network — defensive against early-warmup signals where
        the strategy hasn't picked a symbol yet."""
        if not symbol or not symbol.strip():
            return []
        key = symbol.strip().upper()
        now = datetime.now(timezone.utc)
        cached = self._cache.get(key)
        if cached is not None:
            fetched_at, hints = cached
            if now - fetched_at < self._ttl:
                return hints

        hints = self._fetch_uncached(key)
        self._cache[key] = (now, hints)
        return hints

    def invalidate(self, symbol: str | None = None) -> None:
        """Drop the cache for one symbol or all. The C-3 cockpit's
        Dismiss / Add UI changes the registry; tests + callers that
        want to see the change immediately should call this."""
        if symbol is None:
            self._cache.clear()
            return
        self._cache.pop(symbol.strip().upper(), None)

    # ── internal ─────────────────────────────────────────────────

    def _fetch_uncached(self, symbol: str) -> list[CatalystHint]:
        url = f"{self._api_base}/api/catalysts/"
        params = {
            "symbol": symbol,
            "lookbackDays": str(self._lookback),
            "lookaheadDays": str(self._lookahead),
            "status": "active",
        }
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = self._http_fn(
                url, params=params, headers=headers, timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001
            _log.debug("catalyst fetch threw for %s: %s", symbol, exc)
            return []
        status = getattr(resp, "status_code", 0)
        if not (200 <= status < 300):
            _log.debug(
                "catalyst fetch returned %s for %s", status, symbol,
            )
            return []
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            _log.debug("catalyst response parse failed for %s: %s", symbol, exc)
            return []
        return _to_hints(body.get("catalysts") if isinstance(body, dict) else [])


__all__ = ["CatalystFetcher"]
