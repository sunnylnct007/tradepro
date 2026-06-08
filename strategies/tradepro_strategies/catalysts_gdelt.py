"""GDELT → catalyst registry (Phase 17.1 — the free macro/event source).

The keyword extractor (``catalysts.py``) and the Finnhub source
(``catalysts_finnhub.py``) are both *per-symbol*: they need a ticker and
its news to fire. But the catalysts that actually move the book are
frequently *market-wide* macro events — an FOMC rate decision, a CPI
print, an OPEC+ cut, a major election — that no single-ticker news feed
surfaces cleanly. Those are exactly the events the LLM order-gate needs
in its catalyst overlay (the Ecopetrol 2026-05-21 lesson: "tech said
WAIT, but the Colombia election was the whole trade").

This module fills that gap with the **free, key-less GDELT 2.0 DOC API**
(https://api.gdeltproject.org/api/v2/doc/doc, JSON mode). GDELT indexes
global news every 15 minutes; the DOC API lets us run a handful of
high-signal macro queries and read back dated articles.

Design (mirrors ``catalysts_finnhub.fetch_all_catalysts`` in shape so the
sweep can call either source uniformly):

  * A small, curated set of ``_MACRO_QUERIES`` — one per high-signal
    macro theme (FOMC, CPI, OPEC, ECB, elections, geopolitics). We do
    NOT crawl broadly: GDELT returns thousands of articles per query and
    we only want the strongest, freshest signal per theme.

  * Each query carries a **target symbol** (the market proxy the macro
    event hits — FOMC→SPY, OPEC→XLE, gold→GLD, …) plus a ``kind``
    (``macro`` or ``event``) and a baseline ``severity``. Because the
    .NET registry keys catalysts by symbol, attaching macro events to a
    proxy already in the sweep universe means the overlay surfaces them
    on the relevant context ticker.

  * Articles map to ``Catalyst`` objects with ``occurs_on`` taken
    best-effort from the article's ``seendate`` (GDELT's first-seen
    timestamp) — a proxy for "when this event surfaced". Confidence is
    coarse and theme-driven (macro events are real but not exchange-
    confirmed dates like an earnings calendar).

Best-effort + fail-open EVERYWHERE: any network error, bad JSON, or
unexpected shape returns ``[]`` and logs a warning. A catalyst source
must never break the daily sweep.

No API key. No new dependencies (uses ``requests``, already a dep).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from .catalysts import Catalyst

_log = logging.getLogger("tradepro.catalysts_gdelt")

_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
_TIMEOUT = 12.0
_SOURCE = "gdelt"

# GDELT asks callers to limit to one request every ~5 seconds; exceeding
# it returns a 429 with that exact message. The daily sweep has no
# urgency, so we pause between queries to stay polite (and unblocked).
_INTER_QUERY_DELAY = 5.5

# A generic, descriptive User-Agent. GDELT's DOC API is open but a bare
# python-requests UA occasionally gets throttled; identify ourselves.
_USER_AGENT = "tradepro-catalysts-gdelt/1.0 (+https://github.com/sunnylnct007/tradepro)"


@dataclass(frozen=True)
class MacroQuery:
    """One high-signal macro theme. ``query`` is the GDELT DOC search
    string; ``symbol`` is the market proxy the event hits (so the
    catalyst lands on a ticker the sweep already covers); ``kind`` is
    ``macro`` (scheduled policy/data) or ``event`` (election / shock);
    ``severity`` is the baseline registry severity for the theme."""

    name: str
    query: str
    symbol: str
    kind: str
    severity: str


# Curated, deliberately SMALL. Each theme runs ONE query; we keep the
# strongest few articles per theme so the registry isn't flooded. Extend
# with care — every theme is one extra GDELT call on the daily sweep.
#
# GDELT query syntax: quoted phrases are exact; bare terms are OR-ish.
# We constrain to English to keep the headlines parseable downstream.
_MACRO_QUERIES: tuple[MacroQuery, ...] = (
    MacroQuery(
        name="fomc_rate_decision",
        query='("FOMC" OR "Federal Reserve" OR "rate decision" OR "interest rate decision") sourcelang:english',
        symbol="SPY",
        kind="macro",
        severity="high",
    ),
    MacroQuery(
        name="cpi_inflation",
        query='("CPI" OR "inflation data" OR "inflation report" OR "consumer price index") sourcelang:english',
        symbol="SPY",
        kind="macro",
        severity="medium",
    ),
    MacroQuery(
        name="ecb_rate_decision",
        query='("ECB" OR "European Central Bank" OR "Lagarde") ("rate" OR "policy") sourcelang:english',
        symbol="EFA",
        kind="macro",
        severity="medium",
    ),
    MacroQuery(
        name="opec_oil",
        query='("OPEC" OR "OPEC+") ("output" OR "production cut" OR "supply" OR "quota") sourcelang:english',
        symbol="XLE",
        kind="macro",
        severity="high",
    ),
    MacroQuery(
        name="gold_safe_haven",
        query='("gold price" OR "safe haven" OR "bullion") ("surge" OR "record" OR "rally" OR "plunge") sourcelang:english',
        symbol="GLD",
        kind="macro",
        severity="medium",
    ),
    MacroQuery(
        name="elections",
        query='("presidential election" OR "general election" OR "snap election" OR "runoff vote") sourcelang:english',
        symbol="SPY",
        kind="event",
        severity="high",
    ),
    MacroQuery(
        name="geopolitical_shock",
        query='("sanctions" OR "military strike" OR "ceasefire" OR "geopolitical risk" OR "trade war") sourcelang:english',
        symbol="SPY",
        kind="event",
        severity="high",
    ),
)


# Coarse confidence per severity — macro events are real but not
# exchange-confirmed dates (unlike an earnings calendar), so we cap
# below the Finnhub-earnings 0.95 used for hard calendar dates.
_SEVERITY_CONFIDENCE = {"high": 0.8, "medium": 0.6, "low": 0.45}


@dataclass
class GdeltCatalystReport:
    """Audit-friendly summary of one GDELT fetch pass — mirrors
    ``FinnhubCatalystReport`` so the sweep can log either uniformly."""

    queries_run: int = 0
    articles_seen: int = 0
    catalysts_from_articles: int = 0
    all_catalysts: list[Catalyst] = field(default_factory=list)
    error: str = ""


def _http_get(query: str, *, max_records: int, timeout: float) -> Any:
    """GET the GDELT DOC API in JSON mode. Returns parsed JSON or None
    on ANY error (network, non-2xx, bad JSON). Fail-open by design."""
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max_records),
        "sort": "DateDesc",      # freshest first
        "timespan": "3d",        # only the last 3 days — fresh macro signal
    }
    try:
        resp = requests.get(
            _DOC_API,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _log.warning("GDELT request failed: %s", exc)
        return None
    if not (200 <= resp.status_code < 300):
        _log.warning("GDELT returned %d: %s", resp.status_code, resp.text[:160])
        return None
    try:
        return resp.json()
    except ValueError as exc:
        # GDELT sometimes returns an HTML error page or empty body for a
        # malformed query — treat as "no data", never raise.
        _log.warning("GDELT returned non-JSON body: %s", exc)
        return None


def _parse_seendate(raw: str | None) -> str | None:
    """GDELT ``seendate`` is ``YYYYMMDDTHHMMSSZ`` (e.g.
    ``20260607T143000Z``). Return the ISO date (YYYY-MM-DD), or None
    when the field is missing / unparseable (undated-article handling)."""
    if not raw or not isinstance(raw, str):
        return None
    candidate = raw.strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(candidate, fmt).replace(tzinfo=timezone.utc)
            return dt.date().isoformat()
        except ValueError:
            continue
    return None


def _article_to_catalyst(article: dict, mq: MacroQuery) -> Catalyst | None:
    """Map one GDELT article dict to a Catalyst for the query's theme.
    Returns None for articles with no usable title (best-effort)."""
    title = (article.get("title") or "").strip()
    if not title:
        return None
    seen = article.get("seendate")
    occurs_on = _parse_seendate(seen)
    # surfaced_at: prefer the article's seendate as an ISO datetime;
    # fall back to now() so the freshness field is always populated.
    surfaced_at = None
    if isinstance(seen, str) and seen.strip():
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
            try:
                surfaced_at = (
                    datetime.strptime(seen.strip(), fmt)
                    .replace(tzinfo=timezone.utc)
                    .isoformat()
                )
                break
            except ValueError:
                continue
    if surfaced_at is None:
        surfaced_at = datetime.now(timezone.utc).isoformat()
    return Catalyst(
        kind=mq.kind,
        title=title,
        occurs_on=occurs_on,
        surfaced_at=surfaced_at,
        confidence=_SEVERITY_CONFIDENCE.get(mq.severity, 0.6),
        link=article.get("url"),
        rationale=f"GDELT macro theme '{mq.name}' — {article.get('domain') or 'news'}",
    )


def _dedupe(catalysts: list[Catalyst]) -> list[Catalyst]:
    """Collapse duplicate macro signals. The same theme often returns
    many articles in a day; we keep ONE catalyst per
    (kind, occurs_on, title) — highest confidence wins. This stops a
    single FOMC decision producing 30 near-identical rows."""
    by_key: dict[tuple[str, str | None, str], Catalyst] = {}
    for c in catalysts:
        key = (c.kind, c.occurs_on, c.title.lower())
        existing = by_key.get(key)
        if existing is None or c.confidence > existing.confidence:
            by_key[key] = c
    deduped = list(by_key.values())
    deduped.sort(key=lambda c: (
        c.occurs_on is None,            # dated first
        c.occurs_on or "9999-12-31",    # then by date
        -c.confidence,
    ))
    return deduped


def fetch_catalysts_for_query(
    mq: MacroQuery,
    *,
    max_records: int = 5,
    timeout: float = _TIMEOUT,
) -> list[Catalyst]:
    """Run one macro query against GDELT and map its articles to dated
    Catalyst objects for the query's target symbol. Fail-open: returns
    [] on any error. Caps at ``max_records`` so one theme can't flood."""
    data = _http_get(mq.query, max_records=max_records, timeout=timeout)
    if not data:
        return []
    articles = data.get("articles") if isinstance(data, dict) else None
    if not isinstance(articles, list):
        return []
    out: list[Catalyst] = []
    for art in articles:
        if not isinstance(art, dict):
            continue
        c = _article_to_catalyst(art, mq)
        if c is not None:
            out.append(c)
    return _dedupe(out)


def fetch_all_catalysts(
    *,
    queries: tuple[MacroQuery, ...] = _MACRO_QUERIES,
    max_records_per_query: int = 5,
    timeout: float = _TIMEOUT,
    inter_query_delay: float = _INTER_QUERY_DELAY,
) -> dict[str, GdeltCatalystReport]:
    """Run every curated macro query and return a per-SYMBOL map of
    reports, ready for ``catalysts_sink.post_catalyst``.

    The return shape is keyed by the macro proxy *symbol* (SPY, XLE,
    GLD, …) rather than per-ticker like Finnhub, because GDELT macro
    events are market-wide and the registry stores catalysts per symbol.
    The sweep iterates ``{symbol: report.all_catalysts}`` and posts each
    with ``source="gdelt"``.

    Best-effort: a failing query contributes an empty report and never
    aborts the others.
    """
    by_symbol: dict[str, GdeltCatalystReport] = {}
    for i, mq in enumerate(queries):
        if i > 0 and inter_query_delay > 0:
            # Respect GDELT's ~1-req-per-5s soft limit. Best-effort: the
            # daily cron has no urgency, so a few seconds per theme is fine.
            time.sleep(inter_query_delay)
        report = by_symbol.setdefault(mq.symbol, GdeltCatalystReport())
        report.queries_run += 1
        try:
            cats = fetch_catalysts_for_query(
                mq, max_records=max_records_per_query, timeout=timeout,
            )
        except Exception as exc:  # noqa: BLE001 — never let a theme break the sweep
            _log.warning("GDELT theme %s failed unexpectedly: %s", mq.name, exc)
            report.error = str(exc)
            continue
        report.articles_seen += len(cats)
        report.all_catalysts.extend(cats)

    # Final per-symbol dedupe (two themes can hit the same symbol, e.g.
    # FOMC + CPI + elections all land on SPY) and tally.
    for symbol, report in by_symbol.items():
        report.all_catalysts = _dedupe(report.all_catalysts)
        report.catalysts_from_articles = len(report.all_catalysts)
        _log.info(
            "GDELT %s: %d queries → %d catalysts",
            symbol, report.queries_run, report.catalysts_from_articles,
        )
    return by_symbol


__all__ = [
    "MacroQuery",
    "GdeltCatalystReport",
    "fetch_all_catalysts",
    "fetch_catalysts_for_query",
    "_MACRO_QUERIES",
]
