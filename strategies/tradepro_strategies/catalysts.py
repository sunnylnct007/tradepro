"""Catalyst extraction — Phase 17.2 of DATA_ROADMAP.

Takes a list of news headlines (already fetched via news.py) and
extracts DATED catalysts: events with a specific timeframe that could
move the stock. The Ecopetrol (EC) case 2026-05-21 was the motivating
example: technicals said WAIT but Colombia election in 10 days was
the entire reason the trade existed — TradePro had the headlines but
no concept of "this event has a date".

Today's scope (Phase 17.2):
  - Pure-Python keyword + regex extractor over the headline +
    publisher + published_at trio already on NewsItem.
  - No external API. No LLM call. Deterministic + cheap so it runs
    on every compare row.
  - Identifies five catalyst kinds for v1: election, earnings,
    central_bank (FOMC / ECB / BoE / etc.), commodity (oil / gold
    moves), regulatory (FDA / antitrust / sanctions).

Out of scope for this commit (later phases):
  - GDELT geopolitical-event ingest (Phase 17.1 — separate file).
  - NewsAPI broader coverage (Phase 17.1 — separate file).
  - LLM-based catalyst extraction (Phase 17.5 — uses
    Ollama / FinBERT once they're scoring sentiment side-by-side).
  - Per-ticker relevance scoring of macro events (Phase 17.4).

The output is a per-symbol list of ``Catalyst`` records; the
comparator surfaces these on the row via a new ``catalysts`` field
that the Symbol Deep Dive Section 5 reads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

# Catalyst kinds. Extend with care — the UI will show one icon /
# colour per kind, so adding a new kind needs a UX decision.
CATALYST_KINDS = (
    "election",
    "earnings",
    "central_bank",
    "commodity",
    "regulatory",
)


@dataclass
class Catalyst:
    """A dated event detected in a news headline. ``occurs_on`` is
    the catalyst's own date (e.g. earnings date, election day) when
    we can extract one; ``surfaced_at`` is the timestamp of the
    headline that surfaced it. The UI shows both: "Earnings May 28
    · news 5h ago"."""

    kind: str
    """One of ``CATALYST_KINDS``."""

    title: str
    """The headline that surfaced this catalyst — exact text so the
    user can hover / click to read the source."""

    occurs_on: str | None
    """ISO date (YYYY-MM-DD) of the catalyst itself, or None when
    only relative ("in 10 days", "next week") is known."""

    surfaced_at: str | None
    """ISO datetime when the headline was published. Lets the UI
    surface freshness alongside the catalyst date."""

    confidence: float
    """How confident the extractor is, in [0, 1]. v1 is keyword
    based so the score is coarse — 1.0 for explicit date matches,
    0.7 for relative-date matches, 0.5 for keyword-only."""

    link: str | None = None
    """Source URL."""

    rationale: str = ""
    """One-line explanation of why this matched — the literal
    keyword / regex that fired. Surfaces in the trust tooltip."""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "occurs_on": self.occurs_on,
            "surfaced_at": self.surfaced_at,
            "confidence": self.confidence,
            "link": self.link,
            "rationale": self.rationale,
        }


# --- patterns -----------------------------------------------------------

# Election keywords are deliberately broad — primaries, runoffs,
# referendums all matter. Stop-words after the keyword reduce
# false positives like "the elected officials voted...".
ELECTION_RX = re.compile(
    r"\b("
    r"election|elections|elect(?:ed|ing|s)?|runoff|run-off|"
    r"primary|primaries|referendum|by-?election|"
    r"presidential|parliamentary|general election|"
    r"vote|polling day"
    r")\b",
    re.IGNORECASE,
)

# Earnings catalysts often have a date right in the headline.
EARNINGS_RX = re.compile(
    r"\b("
    r"earnings|quarterly results|q[1-4] (?:results|earnings|report)|"
    r"posts? (?:profit|loss)|beat|miss|guidance|"
    r"reports? (?:earnings|results)|"
    r"profit warning|revenue (?:beat|miss)"
    r")\b",
    re.IGNORECASE,
)

# Central bank actions — both the rate decision itself and pre/post
# commentary.
CENTRAL_BANK_RX = re.compile(
    r"\b("
    r"fed|fomc|federal reserve|ecb|bank of england|boe|"
    r"bank of japan|boj|pboc|swiss national bank|snb|"
    r"rate (?:decision|hike|cut|hold|rise)|"
    r"interest rate|hawkish|dovish|"
    r"powell|lagarde|bailey|ueda"  # current chairs
    r")\b",
    re.IGNORECASE,
)

# Commodities — only flag the ones where the *price* moved, not
# mentions of the commodity itself.
COMMODITY_RX = re.compile(
    r"\b("
    r"oil (?:price|surge|spike|jump|plunge|crash|rally|soar|tumble|slide)|"
    r"crude (?:price|surge|spike|jump|plunge|crash|rally)|"
    r"gold (?:price|surge|spike|jump|plunge|crash|rally|soar|tumble|slide)|"
    r"opec(?:\+)?|"
    r"copper (?:price|surge|spike|crash|rally)|"
    r"(?:wti|brent) (?:at|hits|tops|falls|breaks)"
    r")\b",
    re.IGNORECASE,
)

# Regulatory / legal catalysts.
REGULATORY_RX = re.compile(
    r"\b("
    r"fda (?:approval|approves|approved|rejects|rejection|panel)|"
    r"sec (?:investigation|charges|probe|settlement)|"
    r"antitrust|merger blocked|"
    r"sanctions|sanction(?:ed|s)|"
    r"recall|recalls|class action|"
    r"investig(?:ation|ating)|"
    r"doj |department of justice"
    r")\b",
    re.IGNORECASE,
)

# Dated phrases: "May 28", "in 10 days", "next Tuesday".
ABS_DATE_RX = re.compile(
    r"\b("
    r"(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)\s+\d{1,2}(?:st|nd|rd|th)?"
    r"|"
    r"\d{1,2}\s+(?:january|february|march|april|may|june|july|august|"
    r"september|october|november|december)"
    r")\b",
    re.IGNORECASE,
)

REL_DAYS_RX = re.compile(
    r"\b(?:in\s+)?(\d{1,3})\s+days?\b", re.IGNORECASE,
)

REL_WEEKS_RX = re.compile(
    r"\b(?:in\s+)?(\d{1,2})\s+weeks?\b", re.IGNORECASE,
)


def _kind_match(text: str) -> tuple[str, str] | None:
    """Return (kind, matched_keyword) for the first pattern that fires.
    Order matters — earnings first because "earnings beat" should not
    also fire central_bank via "beat"."""
    for kind, rx in (
        ("election", ELECTION_RX),
        ("earnings", EARNINGS_RX),
        ("central_bank", CENTRAL_BANK_RX),
        ("commodity", COMMODITY_RX),
        ("regulatory", REGULATORY_RX),
    ):
        m = rx.search(text)
        if m:
            return kind, m.group(1)
    return None


def _extract_date(text: str, surfaced_at: str | None) -> tuple[str | None, float]:
    """Pull an ISO date out of the headline, return (date, score-boost).
    Falls back to (None, 0.5) when no date is parseable."""

    # Absolute date — "May 28" or "28 May". Resolve year by anchoring
    # to surfaced_at when present; otherwise use current year.
    m_abs = ABS_DATE_RX.search(text)
    if m_abs:
        parsed = _parse_loose_date(m_abs.group(1), surfaced_at)
        if parsed:
            return parsed.date().isoformat(), 1.0

    # Relative — "in 10 days", "in 2 weeks".
    base = _parse_iso(surfaced_at) or datetime.now(timezone.utc)
    m_days = REL_DAYS_RX.search(text)
    if m_days:
        try:
            n = int(m_days.group(1))
            if 0 < n <= 365:
                return (base + timedelta(days=n)).date().isoformat(), 0.7
        except ValueError:
            pass
    m_weeks = REL_WEEKS_RX.search(text)
    if m_weeks:
        try:
            n = int(m_weeks.group(1))
            if 0 < n <= 52:
                return (base + timedelta(weeks=n)).date().isoformat(), 0.7
        except ValueError:
            pass

    return None, 0.5


def _parse_loose_date(s: str, surfaced_at: str | None) -> datetime | None:
    """Loose date parsing — handles 'May 28', '28 May' with year
    inferred from surfaced_at (else current year)."""
    anchor = _parse_iso(surfaced_at) or datetime.now(timezone.utc)
    for fmt in ("%B %d", "%B %d %Y", "%d %B", "%d %B %Y"):
        try:
            # strptime ignores ordinal suffixes once we strip them
            clean = re.sub(r"(st|nd|rd|th)\b", "", s, flags=re.IGNORECASE).strip()
            dt = datetime.strptime(clean, fmt)
            if dt.year == 1900:  # default year filled by strptime
                dt = dt.replace(year=anchor.year)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def extract_catalysts(news_items: Iterable) -> list[Catalyst]:
    """Walk a list of NewsItem-shaped objects (dicts or dataclasses)
    and emit Catalyst records. Tolerant of either shape so callers
    don't have to convert before passing in.

    De-duplication: when multiple headlines surface the same
    (kind, occurs_on) catalyst, the highest-confidence one wins
    and the rest are dropped. Prevents "5 articles about Apple
    earnings" from showing as 5 separate catalysts on the row."""
    out: list[Catalyst] = []
    for raw in news_items or []:
        item = _as_dict(raw)
        if not item:
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        match = _kind_match(title)
        if not match:
            continue
        kind, kw = match
        surfaced_at = item.get("published_at")
        occurs_on, conf_boost = _extract_date(title, surfaced_at)
        out.append(Catalyst(
            kind=kind,
            title=title,
            occurs_on=occurs_on,
            surfaced_at=surfaced_at,
            confidence=conf_boost,
            link=item.get("link"),
            rationale=f"matched '{kw}'",
        ))

    # De-dup by (kind, occurs_on) — keep highest confidence.
    by_key: dict[tuple[str, str | None], Catalyst] = {}
    for c in out:
        key = (c.kind, c.occurs_on)
        existing = by_key.get(key)
        if existing is None or c.confidence > existing.confidence:
            by_key[key] = c
    # Sort: explicit-date catalysts first (sooner = more urgent),
    # then keyword-only matches.
    deduped = list(by_key.values())
    deduped.sort(key=lambda c: (
        c.occurs_on is None,            # dated catalysts first
        c.occurs_on or "9999-12-31",    # then by date ascending
        -c.confidence,                  # tie-break by confidence
    ))
    return deduped


def _as_dict(raw) -> dict | None:
    """Accept either a dict (already-serialised NewsItem) or a
    dataclass with .title/.link/.published_at. Anything else: drop."""
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "to_dict") and callable(raw.to_dict):
        try:
            return raw.to_dict()
        except Exception:  # noqa: BLE001 — best-effort
            return None
    # last-ditch: pull attributes directly
    if hasattr(raw, "title"):
        return {
            "title": getattr(raw, "title", None),
            "link": getattr(raw, "link", None),
            "published_at": getattr(raw, "published_at", None),
            "publisher": getattr(raw, "publisher", None),
        }
    return None


__all__ = ["Catalyst", "CATALYST_KINDS", "extract_catalysts"]
