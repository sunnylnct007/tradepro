"""Config-driven news-site fallback — crawl latest headlines from CONFIGURED
feeds when the primary earnings/news APIs fail (the MA case: Finnhub/yfinance
returned no date for a mega-cap that reported the day before).

Sources are CONFIG, not code (feedback_config_driven_no_hardcoding):
`TRADEPRO_NEWS_FEEDS` = comma-separated URL templates with a `{symbol}`
placeholder. Defaults to Yahoo Finance's per-symbol RSS. Any RSS/Atom-ish XML
with <item><title>/<pubDate> works; a site that fails just contributes nothing.

Used by the earnings gate as a VERIFICATION hint (did this name just report?),
never as a silent decision-maker: a positive mention escalates UNKNOWN to a
veto; no mention keeps the name visible with an EARNINGS_UNVERIFIED alert.
Stdlib-only (urllib + xml.etree) — no new dependencies.
"""
from __future__ import annotations

import datetime as _dt
import email.utils as _eut
import logging
import os
import re
import urllib.request
import xml.etree.ElementTree as _ET

log = logging.getLogger("tradepro.news_sites")

DEFAULT_FEEDS = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US",
)

# Headline patterns that indicate an EARNINGS REPORT event (not a preview).
_EARNINGS_RE = re.compile(
    r"\b(earnings|quarterly results|Q[1-4]\s+(results|earnings|revenue)|"
    r"beats?\s+(estimates|expectations|the\s+street)|"
    r"misses?\s+(estimates|expectations)|"
    r"reports?\s+(first|second|third|fourth)[-\s]quarter|"
    r"(raises|cuts|lifts)\s+(guidance|outlook|forecast))\b",
    re.IGNORECASE,
)


def configured_feeds() -> list[str]:
    """Feed URL templates from TRADEPRO_NEWS_FEEDS (comma-separated, `{symbol}`
    placeholder) or the defaults."""
    raw = os.environ.get("TRADEPRO_NEWS_FEEDS", "").strip()
    if not raw:
        return list(DEFAULT_FEEDS)
    return [u.strip() for u in raw.split(",") if u.strip()]


def _parse_feed(xml_bytes: bytes) -> list[dict]:
    """<item><title>/<pubDate> (RSS) or <entry><title>/<updated> (Atom) →
    [{title, published(datetime|None)}]. Best-effort; bad XML → []."""
    out: list[dict] = []
    try:
        root = _ET.fromstring(xml_bytes)
    except _ET.ParseError:
        return out
    # RSS <item> anywhere; Atom entries carry a namespace — match by localname.
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        title, published = None, None
        for child in el:
            ctag = child.tag.rsplit("}", 1)[-1]
            if ctag == "title" and child.text:
                title = child.text.strip()
            elif ctag in ("pubDate", "updated", "published") and child.text:
                txt = child.text.strip()
                try:
                    published = _eut.parsedate_to_datetime(txt)
                except (TypeError, ValueError):
                    try:
                        published = _dt.datetime.fromisoformat(txt.replace("Z", "+00:00"))
                    except ValueError:
                        published = None
        if title:
            out.append({"title": title, "published": published})
    return out


def fetch_recent_headlines(symbol: str, *, timeout: float = 8.0) -> list[dict] | None:
    """Latest headlines for `symbol` across every configured feed.
    Returns None when EVERY feed failed (feed outage ≠ "no news" — fail-loud),
    else the merged list (possibly empty)."""
    merged: list[dict] = []
    any_ok = False
    for tpl in configured_feeds():
        url = tpl.format(symbol=symbol)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tradepro/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                merged.extend(_parse_feed(resp.read()))
            any_ok = True
        except Exception as exc:  # noqa: BLE001 — a dead site contributes nothing
            log.debug("news feed failed (%s): %s", url, exc)
    return merged if any_ok else None


def recent_earnings_mention(symbol: str, *, lookback_days: int = 3) -> bool | None:
    """Did the configured news feeds mention an EARNINGS REPORT for `symbol`
    within `lookback_days`? True/False, or None when all feeds failed (unknown —
    caller must not treat an outage as 'no news')."""
    heads = fetch_recent_headlines(symbol)
    if heads is None:
        return None
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=lookback_days)
    for h in heads:
        if not _EARNINGS_RE.search(h["title"] or ""):
            continue
        pub = h.get("published")
        if pub is None:
            continue                      # undated headline can't prove recency
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=_dt.timezone.utc)
        if pub >= cutoff:
            return True
    return False
