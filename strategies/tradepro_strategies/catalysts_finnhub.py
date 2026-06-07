"""Finnhub → catalyst registry (C-4).

Two Finnhub free-tier endpoints feed the catalyst registry:

1. ``/company-news`` — recent headlines per symbol. Items run
   through the existing keyword extractor (``catalysts.extract_catalysts``)
   to surface dated events embedded in news text.

2. ``/calendar/earnings`` — official earnings calendar. Every upcoming
   earnings date is a HIGH-confidence catalyst (no keyword matching
   needed — the date is an explicit fact from the exchange).

Both paths are additive: they produce ``Catalyst`` objects compatible
with the existing ``catalysts_sink.extract_and_post`` path so the
existing POST + idempotent UPSERT logic works unchanged.

Credentials
-----------
Reads ``TRADEPRO_FINNHUB_API_KEY`` env var (same name as the bars
source). Falls back to ``get_secret("finnhub-api-key")`` (AWS
Secrets Manager) when the env var is absent. Returns an empty list
when no key is found — fail-open so the Yahoo sweep still runs.

Rate limits (free tier)
-----------------------
60 calls/min, 30 calls/sec. We add a small inter-call delay (200ms)
and batch symbols in chunks to stay well under the limit. The caller
(daily sweep) is responsible for chunking when operating on the full
universe.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .catalysts import Catalyst, extract_catalysts
from .news import NewsItem

_log = logging.getLogger("tradepro.catalysts_finnhub")

_BASE = "https://finnhub.io/api/v1"
_REQUEST_DELAY = 0.22   # seconds between calls — stays well under 30/sec
_TIMEOUT = 10.0


def _get_api_key() -> str:
    """Resolve Finnhub API key: env var first, then AWS Secrets Manager."""
    key = os.environ.get("TRADEPRO_FINNHUB_API_KEY", "")
    if key:
        return key
    try:
        from .secrets import get_secret
        return get_secret("finnhub-api-key") or ""
    except Exception:
        return ""


def _get(path: str, params: dict[str, Any], *, api_key: str) -> Any:
    """GET a Finnhub endpoint. Returns parsed JSON or None on error."""
    params["token"] = api_key
    url = _BASE + path
    try:
        r = requests.get(url, params=params, timeout=_TIMEOUT)
        if r.status_code == 429:
            _log.warning("Finnhub rate-limited on %s — skipping", path)
            return None
        if not r.ok:
            _log.warning("Finnhub %s returned %d: %s", path, r.status_code, r.text[:120])
            return None
        return r.json()
    except Exception as exc:
        _log.warning("Finnhub %s failed: %s", path, exc)
        return None


# ── Company news ──────────────────────────────────────────────────────────────

def fetch_company_news(
    symbol: str,
    *,
    api_key: str,
    days_back: int = 7,
) -> list[NewsItem]:
    """Fetch recent Finnhub company-news headlines for ``symbol``.
    Returns a list of ``NewsItem`` objects compatible with the existing
    ``extract_catalysts`` keyword extractor."""
    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = today.isoformat()
    data = _get("/company-news", {"symbol": symbol, "from": frm, "to": to}, api_key=api_key)
    time.sleep(_REQUEST_DELAY)
    if not data:
        return []
    items: list[NewsItem] = []
    for row in data:
        ts = row.get("datetime")
        published = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts else None
        )
        items.append(NewsItem(
            title=row.get("headline", ""),
            publisher=row.get("source"),
            link=row.get("url"),
            published_at=published,
        ))
    return items


def extract_catalysts_from_news(
    symbol: str,
    news_items: list[NewsItem],
) -> list[Catalyst]:
    """Run the existing keyword extractor over Finnhub news items."""
    return extract_catalysts(news_items)


# ── Earnings calendar ─────────────────────────────────────────────────────────

def fetch_upcoming_earnings(
    symbol: str,
    *,
    api_key: str,
    days_ahead: int = 90,
) -> list[Catalyst]:
    """Fetch the upcoming earnings date from Finnhub's calendar.

    Unlike news-based extraction this is HIGH confidence (0.95) — the
    date comes from the exchange calendar, not keyword matching. Every
    row becomes one ``Catalyst(kind='earnings', ...)`` directly."""
    today = datetime.now(timezone.utc).date()
    to = (today + timedelta(days=days_ahead)).isoformat()
    data = _get(
        "/calendar/earnings",
        {"symbol": symbol, "from": today.isoformat(), "to": to},
        api_key=api_key,
    )
    time.sleep(_REQUEST_DELAY)
    if not data:
        return []
    events = data.get("earningsCalendar") or []
    catalysts: list[Catalyst] = []
    for ev in events:
        date_str = ev.get("date")
        if not date_str:
            continue
        hour = ev.get("hour", "")          # "bmo" = before market, "amc" = after
        hour_label = {"bmo": " (pre-market)", "amc": " (after-hours)"}.get(hour, "")
        est = ev.get("epsEstimate")
        est_label = f" — EPS est {est:.2f}" if est is not None else ""
        title = f"{symbol} earnings{hour_label}{est_label} · {date_str}"
        rationale = "Finnhub earnings calendar — exact date from exchange"
        catalysts.append(Catalyst(
            kind="earnings",
            title=title,
            occurs_on=date_str,
            surfaced_at=datetime.now(timezone.utc).isoformat(),
            confidence=0.95,     # explicit calendar date → HIGH
            link=None,
            rationale=rationale,
        ))
    return catalysts


# ── Combined fetch ────────────────────────────────────────────────────────────

@dataclass
class FinnhubCatalystReport:
    symbol: str
    news_items: int = 0
    catalysts_from_news: int = 0
    catalysts_from_earnings: int = 0
    all_catalysts: list[Catalyst] = field(default_factory=list)
    skipped: bool = False           # True when no API key
    error: str = ""


def fetch_all_catalysts(
    symbol: str,
    *,
    api_key: str = "",
    days_back: int = 7,
    days_ahead: int = 90,
) -> FinnhubCatalystReport:
    """One-stop call: fetch news + earnings for ``symbol`` and return
    combined catalysts ready for ``catalysts_sink.post_catalyst``."""
    key = api_key or _get_api_key()
    if not key:
        _log.info("No Finnhub API key — skipping Finnhub catalyst fetch for %s", symbol)
        return FinnhubCatalystReport(symbol=symbol, skipped=True)

    report = FinnhubCatalystReport(symbol=symbol)

    # ── Company news → keyword extractor ─────────────────────────────
    news = fetch_company_news(symbol, api_key=key, days_back=days_back)
    report.news_items = len(news)
    news_catalysts = extract_catalysts_from_news(symbol, news)
    report.catalysts_from_news = len(news_catalysts)

    # ── Earnings calendar (direct) ────────────────────────────────────
    earnings_catalysts = fetch_upcoming_earnings(symbol, api_key=key, days_ahead=days_ahead)
    report.catalysts_from_earnings = len(earnings_catalysts)

    report.all_catalysts = news_catalysts + earnings_catalysts

    _log.info(
        "%s: %d news → %d catalysts, %d earnings dates",
        symbol, report.news_items, report.catalysts_from_news,
        report.catalysts_from_earnings,
    )
    return report


__all__ = [
    "fetch_all_catalysts",
    "fetch_company_news",
    "fetch_upcoming_earnings",
    "FinnhubCatalystReport",
]
