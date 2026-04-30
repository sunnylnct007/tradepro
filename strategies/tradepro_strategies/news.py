"""Per-symbol news headlines (no sentiment scoring — that's Phase 6).

Pulled from Yahoo's news feed via yfinance.Ticker.news. Returns the
most recent N items so a user can see *what's been said* about a
symbol alongside the rule-based verdict, without the system pretending
to interpret the news.

Each item:
- title       — headline
- publisher   — Reuters / Yahoo Finance / Bloomberg / etc.
- link        — URL to the source article
- published   — ISO timestamp
- thumbnail   — small preview image URL (best-effort)

Schema diverges across yfinance versions — newer versions wrap
everything under `content.{title,clickThroughUrl,...}`. We try both
shapes and surface whatever is present.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class NewsItem:
    title: str
    publisher: str | None
    link: str | None
    published_at: str | None
    thumbnail: str | None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "publisher": self.publisher,
            "link": self.link,
            "published_at": self.published_at,
            "thumbnail": self.thumbnail,
        }


def _epoch_to_iso(epoch) -> str | None:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _from_legacy(raw: dict) -> NewsItem | None:
    title = raw.get("title")
    if not title:
        return None
    thumb = None
    th = raw.get("thumbnail") or {}
    res = th.get("resolutions") or [] if isinstance(th, dict) else []
    if res:
        thumb = res[0].get("url")
    return NewsItem(
        title=title,
        publisher=raw.get("publisher"),
        link=raw.get("link"),
        published_at=_epoch_to_iso(raw.get("providerPublishTime")),
        thumbnail=thumb,
    )


def _from_modern(raw: dict) -> NewsItem | None:
    """Newer yfinance shape: items are { id, content: { title, ... } }."""
    content = raw.get("content") or {}
    title = content.get("title")
    if not title:
        return None
    provider = (content.get("provider") or {}).get("displayName")
    click = content.get("clickThroughUrl") or {}
    link = click.get("url") if isinstance(click, dict) else None
    if not link:
        link = (content.get("canonicalUrl") or {}).get("url") if isinstance(content.get("canonicalUrl"), dict) else None
    published = content.get("pubDate") or content.get("displayTime")
    # pubDate may be ISO already; pass-through if so, else try epoch.
    if isinstance(published, str) and "T" in published:
        published_at = published
    else:
        published_at = _epoch_to_iso(published)
    thumb = None
    th = content.get("thumbnail") or {}
    if isinstance(th, dict):
        res = th.get("resolutions") or []
        if res:
            thumb = res[0].get("url")
    return NewsItem(
        title=title,
        publisher=provider,
        link=link,
        published_at=published_at,
        thumbnail=thumb,
    )


def fetch_news(symbol: str, limit: int = 8) -> list[NewsItem]:
    """Best-effort. Returns [] on any failure — empty headlines are not
    a reason to fail the comparator run."""
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:  # noqa: BLE001
        return []

    out: list[NewsItem] = []
    for raw in items[:limit]:
        if not isinstance(raw, dict):
            continue
        # Modern shape first (post-yfinance 0.2.40-ish).
        item = _from_modern(raw) if "content" in raw else None
        if item is None:
            item = _from_legacy(raw)
        if item is not None:
            out.append(item)
    return out
