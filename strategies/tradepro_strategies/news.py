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
from datetime import datetime, timedelta, timezone


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


# ETF → benchmark / theme-proxy used as a news fallback when Yahoo
# returns nothing for the ETF itself. Index ETFs like VUKE/ISF
# (FTSE 100) have no fund-level news but the index they track
# generates plenty of it. Mapping is conservative — only includes
# symbols where the fallback's news is genuinely relevant to the ETF
# (S&P 500 trackers → ^GSPC). Niche / multi-region ETFs without a
# clean proxy stay un-mapped and just show empty news, which is
# honest. Bug #13.
NEWS_FALLBACK: dict[str, str] = {
    # UK FTSE 100 trackers
    "VUKE.L": "^FTSE", "ISF.L": "^FTSE", "IUKD.L": "^FTSE",
    # UK FTSE 250
    "VMID.L": "^FTMC",
    # S&P 500 trackers (LSE-listed)
    "VUSA.L": "^GSPC", "CSPX.L": "^GSPC",
    # MSCI World trackers — US dominates the index so S&P-500 news is
    # the most relevant proxy
    "VWRP.L": "^GSPC", "VWRL.L": "^GSPC", "SWDA.L": "^GSPC",
    "HMWO.L": "^GSPC", "SWLD.L": "^GSPC",
    # US S&P 500
    "VOO": "^GSPC", "IVV": "^GSPC", "VTI": "^GSPC", "SCHD": "^GSPC",
    # Nasdaq 100
    "QQQ": "^IXIC",
    # Russell 2000
    "IWM": "^RUT",
    # Europe
    "VEUR.L": "^STOXX",
    # Japan
    "VJPN.L": "^N225",
    # Gold
    "IGLN.L": "GC=F", "GLD": "GC=F",
    # UK Gilts → US Treasuries proxy (different curves, but bond-market
    # tone tends to correlate; better than nothing)
    "IGLT.L": "TLT",
    # Clean energy thematic — INRG.L (UK) → ICLN (US) tracks the same
    # theme with deeper news coverage
    "INRG.L": "ICLN",
}


def fetch_news(
    symbol: str,
    limit: int = 8,
    max_age_days: int = 14,
) -> list[NewsItem]:
    """Best-effort. Returns [] on any failure — empty headlines are not
    a reason to fail the comparator run.

    Yahoo's `Ticker.news` returns whatever is in its store, in no
    particular order, with no freshness guarantee — users reported
    seeing 55-day-old items rendered as if current. To compensate:
      * over-fetch (3× limit) so we have headroom after filtering,
      * drop items older than `max_age_days` (default 14d),
      * sort newest-first,
      * trim to `limit`.

    Items with no `published_at` are kept and treated as recent
    (Yahoo's older shape sometimes omits the timestamp; dropping them
    would empty the news list for those symbols).
    """
    try:
        import yfinance as yf
    except ImportError:
        return []
    try:
        items = yf.Ticker(symbol).news or []
    except Exception:  # noqa: BLE001
        return []

    parsed: list[NewsItem] = []
    # Over-fetch — yfinance ignores extra so this is a no-op when the
    # store has fewer than 3*limit items, but on liquid names it gives
    # us a wider candidate pool to drop old items from.
    for raw in items[: max(limit * 3, limit)]:
        if not isinstance(raw, dict):
            continue
        item = _from_modern(raw) if "content" in raw else None
        if item is None:
            item = _from_legacy(raw)
        if item is not None:
            parsed.append(item)

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)

    def _ts(item: NewsItem) -> datetime | None:
        if not item.published_at:
            return None
        try:
            ts = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return None

    fresh: list[tuple[NewsItem, datetime | None]] = []
    for item in parsed:
        ts = _ts(item)
        if ts is None or ts >= cutoff:
            fresh.append((item, ts))

    # Sort: timestamped items newest-first, untimestamped items last.
    # `datetime.min` sorts oldest, so we use (-ts.timestamp(), 0) for
    # timestamped and (inf, 1) for untimestamped to keep them at the
    # tail without losing them entirely.
    fresh.sort(key=lambda pair: (
        -(pair[1].timestamp() if pair[1] else float("-inf")),
        0 if pair[1] else 1,
    ))
    return [item for item, _ in fresh[:limit]]


def fetch_news_with_fallback(
    symbol: str,
    limit: int = 8,
    max_age_days: int = 14,
) -> tuple[list[NewsItem], str | None]:
    """Like fetch_news but transparently falls back to an index/sector
    proxy when the symbol itself returns no fresh news.

    Returns (items, fallback_used). When fallback_used is non-None,
    `items` came from the proxy symbol and the renderer should label
    them (e.g. "via ^FTSE") so the user doesn't think Apple news is
    about VUKE.L.

    Bug #13. ETFs like VUKE.L / SWDA.L have no fund-level Yahoo news
    — without the fallback their news card sits empty forever.
    """
    primary = fetch_news(symbol, limit=limit, max_age_days=max_age_days)
    if primary:
        return primary, None
    fallback = NEWS_FALLBACK.get(symbol.upper())
    if not fallback:
        return [], None
    proxy_items = fetch_news(fallback, limit=limit, max_age_days=max_age_days)
    return proxy_items, (fallback if proxy_items else None)
