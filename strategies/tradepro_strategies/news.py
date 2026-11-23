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


def _fetch_yahoo(
    symbol: str,
    limit: int = 8,
    max_age_days: int = 14,
) -> tuple[list[NewsItem], str | None]:
    """Yahoo headlines and, when the read failed, why.

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
        return [], "yahoo: yfinance not installed"
    try:
        items = yf.Ticker(symbol).news or []
    except Exception as exc:  # noqa: BLE001
        return [], f"yahoo: {type(exc).__name__}: {str(exc).strip('* ')[:80]}"

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
    return [item for item, _ in fresh[:limit]], None


# ── one read, every source, and the reason when it comes back empty ─────
# 20 Nov 2026, the worst AI sell-off week of the year: the desk's news read
# returned ZERO headlines for NVDA, AMD and SPY. Yahoo answered "currently
# down"; the Finnhub fallback needed a key the Mac never had; both failures
# were swallowed into [], which every caller rendered as a quiet tape and the
# sentiment scorer read as "nothing material". A read that FAILED is not "no
# news" — it now says which source failed and why, and the backend (which holds
# the Finnhub key in AWS) is the second source wherever this runs.

@dataclass
class NewsRead:
    items: list[NewsItem]
    source: str | None
    errors: list[str]

    @property
    def failed(self) -> bool:
        """Nothing came back AND at least one source could not be read."""
        return not self.items and bool(self.errors)

    def to_dict(self) -> dict:
        return {"source": self.source, "errors": self.errors, "failed": self.failed,
                "count": len(self.items)}


def _finnhub_via_api(symbol: str, limit: int, days: int) -> tuple[list[NewsItem], str | None]:
    """Finnhub company news through the TradePro API, which holds the key."""
    try:
        import requests

        base, token = _api_base_and_token()
        r = requests.get(f"{base.rstrip('/')}/api/integrations/finnhub/company-news",
                         params={"symbol": symbol, "days": days},
                         headers={"Authorization": f"Bearer {token}"} if token else {},
                         timeout=20)
        if r.status_code != 200:
            return [], f"finnhub via api: HTTP {r.status_code}"
        d = r.json()
        if d.get("error"):
            return [], f"finnhub via api: {d['error']}"
        out = []
        for row in d.get("items") or []:
            ts = row.get("datetime")
            out.append(NewsItem(
                title=row.get("headline") or "",
                publisher=row.get("source"),
                link=row.get("url"),
                published_at=(datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                              if ts else None),
                thumbnail=None))
        out.sort(key=lambda i: i.published_at or "", reverse=True)
        return out[:limit], None
    except Exception as exc:  # noqa: BLE001
        return [], f"finnhub via api: {type(exc).__name__}: {str(exc)[:120]}"


def _api_base_and_token() -> tuple[str, str | None]:
    """The API the MCP tools talk to. The remote MCP container has no
    ~/.tradepro/credentials: it is configured by TRADEPRO_API_URL (+ token), so
    that is tried FIRST; the laptop's credential chain is the fallback. Before
    this, the container's Finnhub fallback failed on a credentials lookup and
    a symbol in a sell-off read as 'no news' (owner: "claude complained about
    the news endpoint", 23 Nov 2026)."""
    import os
    url = os.environ.get("TRADEPRO_API_URL")
    if url:
        try:
            from .mcp.tools import _resolve_api_token
            return url, _resolve_api_token()
        except Exception:  # noqa: BLE001
            return url, os.environ.get("TRADEPRO_API_TOKEN")
    from .cli.push_to_api import load_credentials
    return load_credentials()


def _finnhub(symbol: str, limit: int, max_age_days: int) -> tuple[list[NewsItem], str | None]:
    import os
    key = os.environ.get("TRADEPRO_FINNHUB_API_KEY", "")
    days = max(1, min(max_age_days, 7))
    if not key:
        return _finnhub_via_api(symbol, limit, days)
    try:
        from .catalysts_finnhub import fetch_company_news
        return fetch_company_news(symbol, api_key=key, days_back=days)[:limit], None
    except Exception as exc:  # noqa: BLE001
        return [], f"finnhub: {type(exc).__name__}"


def read_news(symbol: str, limit: int = 8, max_age_days: int = 14) -> NewsRead:
    """Yahoo, then Finnhub. Returns the first source with headlines, and the
    errors of every source that could not be read."""
    errors: list[str] = []
    for name, fn in (("yahoo", _fetch_yahoo), ("finnhub", _finnhub)):
        items, err = fn(symbol, limit, max_age_days)
        if err:
            errors.append(err)
        if items:
            return NewsRead(items, name, errors)
    return NewsRead([], None, errors)


def fetch_news(
    symbol: str,
    limit: int = 8,
    max_age_days: int = 14,
) -> list[NewsItem]:
    """Headlines from the first source that has them. Kept as a list for its
    callers; a read where every source FAILED is logged loudly, never passed
    off silently as a quiet tape. Callers that must tell the two apart use
    read_news()."""
    r = read_news(symbol, limit=limit, max_age_days=max_age_days)
    if r.failed:
        import logging
        logging.getLogger("tradepro.news").warning(
            "news for %s could NOT be read (not 'no news'): %s", symbol, "; ".join(r.errors))
    return r.items


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
    # Multi-source enrichment (2026-06-14): when Yahoo has nothing for the
    # symbol, pull its REAL company news from Finnhub before resorting to a
    # sector proxy — it's the symbol's own news, just a different source, so it's
    # strictly better than "via ^FTSE". Fires ONLY for empty-Yahoo names, so it
    # adds at most one Finnhub call per sparse symbol (not one per symbol).
    fallback = NEWS_FALLBACK.get(symbol.upper())
    if not fallback:
        return [], None
    proxy_items = fetch_news(fallback, limit=limit, max_age_days=max_age_days)
    return proxy_items, (fallback if proxy_items else None)
