"""Uniform provenance — one shape for "where did this number come from?".

Owner, 15 Aug 2026: *"we shouldn't hit issues where we don't know if data is
coming from cache, yahoo or ibkr."*

Every input behind a trading decision already knows its own origin somewhere
in the stack — the bar store stamps a ``source`` column on each bar, the chain
fetch knows which of g3/ibkr/yfinance answered, the IV solve reports whether
it solved or trusted the broker. What was missing is a SINGLE shape those
answers are reported in, so a row can be read at a glance instead of
reverse-engineered from five differently-named fields.

This module is that shape. It is pure — no I/O, no clock reads except the
``now`` you pass — so it is cheap to unit-test and safe to call per row.

The grade (``trust``) is deliberately coarse and honest:

  golden       IBKR, directly or via TradePro's own OAuth chain feed. The
               standing rule: [[feedback_ibkr_golden_source_yahoo_fallback]].
  derived      WE computed it (solved IV, realised vol from closes). Not
               fetched from anyone; reproducible by hand.
  vendor       A non-broker source that is the RIGHT one for this input
               because the broker serves no such feed — the earnings
               calendar, chiefly. Not a degradation; not golden either.
  fallback     yfinance / IG / the legacy yahoo cache standing in for a feed
               IBKR *does* serve. Real data, lower standing — and it must be
               VISIBLE, never a silent default.
  carried      A real number from an EARLIER moment, reused. Informative,
               never actionable.
  unavailable  Nobody served it. Says so; never a guess.

Only `fallback`, `carried` and `unavailable` are "weak" — they are what the
row summary names. Grading the earnings calendar as a degradation would make
every single-name row read weak and the signal would stop meaning anything.

There is no "unknown" grade on purpose. If a caller cannot name the source,
that is itself a defect worth seeing, and `describe()` grades it `unavailable`
with the reason in the detail line.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Who counts as the golden source. `g3` is TradePro's own API-hosted chain
# feed, which is IBKR OAuth underneath — the same broker, our transport.
GOLDEN_SOURCES = frozenset({"ibkr", "ibkr_web", "g3", "ibkr_oauth",
                            # the broker's own IV field, taken unverified —
                            # golden by ORIGIN; the detail line says it could
                            # not be cross-checked.
                            "broker_only",
                            # TradePro's own capture of a g3/IBKR chain quote
                            # (option_quote_daily). Golden by ORIGIN — verified
                            # 16 Aug 2026: every captured row carries
                            # source='g3_chain'. Used for OPEN INTEREST, which
                            # OCC publishes ONCE A DAY and which therefore does
                            # not tick: a value captured at the last close IS
                            # the current value, so there is no staleness to
                            # mark down. Do NOT extend this token to prices.
                            "own_capture"})
FALLBACK_SOURCES = frozenset({"yfinance", "yahoo", "legacy_yahoo_cache", "ig"})
DERIVED_SOURCES = frozenset({"solved", "solved_only", "cross_checked",
                             "computed", "computed_from_closes", "DISAGREEMENT",
                             "structural"})
# Non-broker feeds that are the APPROPRIATE source for their input because
# IBKR serves no equivalent — not a fallback, not golden.
VENDOR_SOURCES = frozenset({"earnings_calendar", "finnhub"})

# Human names for the raw source tokens, so a row never shows a code word.
_SOURCE_LABELS = {
    "ibkr_web": "IBKR (Web API)",
    "ibkr": "IBKR (Gateway)",
    "ibkr_oauth": "IBKR (OAuth)",
    "g3": "IBKR via TradePro chain feed",
    "yfinance": "Yahoo (fallback)",
    "yahoo": "Yahoo (fallback)",
    "legacy_yahoo_cache": "LEGACY yahoo cache (fallback of last resort)",
    "ig": "IG (fallback)",
    "cache": "bar cache",
    "solved": "solved by TradePro",
    "solved_only": "solved by TradePro",
    "computed": "computed by TradePro",
    "computed_from_closes": "computed from our own closes",
    "broker_only": "IBKR (broker IV, unverified)",
    "own_capture": "IBKR via TradePro's own daily capture",
    "cross_checked": "solved by TradePro, cross-checked vs broker",
    "DISAGREEMENT": "solved by TradePro — DISAGREES with the broker",
    "earnings_calendar": "TradePro earnings calendar (confirmed dates)",
    "finnhub": "Finnhub",
    "structural": "structural — a fact of the security type",
    "carried_last_live": "carried from the last priced screen",
}


def grade(source: str | None) -> str:
    """Trust grade for a raw source token. Unknown tokens grade `fallback`,
    not `golden` — an unrecognised source must never be promoted by accident."""
    if not source:
        return "unavailable"
    s = source.strip()
    if s in GOLDEN_SOURCES:
        return "golden"
    if s in DERIVED_SOURCES:
        return "derived"
    if s in VENDOR_SOURCES:
        return "vendor"
    if s in FALLBACK_SOURCES:
        return "fallback"
    if s.startswith("carried"):
        return "carried"
    return "fallback"


def source_label(source: str | None) -> str:
    if not source:
        return "unavailable"
    return _SOURCE_LABELS.get(source.strip(), source.strip())


def age_text(as_of: datetime | None, now: datetime | None = None) -> str | None:
    """Age in plain words. Deliberately unambiguous about DAYS vs HOURS —
    the 12 Aug garbage-bar alarms were unreadable precisely because "old"
    was never quantified (see the 15 Aug fix, 12b77c3)."""
    if as_of is None:
        return None
    now = now or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    secs = (now - as_of).total_seconds()
    if secs < 0:
        return "dated in the future"
    if secs < 3600:
        return f"{int(secs // 60)}m old"
    if secs < 86400:
        return f"{secs / 3600:.1f}h old"
    days = secs / 86400.0
    if days < 2:
        return "1 day old"
    return f"{days:.0f} days old"


@dataclass
class Provenance:
    """One input's origin. `input` is the machine key; everything else is for
    a human reading the row."""
    input: str
    label: str
    source: str | None
    trust: str
    detail: str
    as_of: str | None = None
    age: str | None = None

    def to_dict(self) -> dict:
        return {
            "input": self.input, "label": self.label,
            "source": self.source, "source_label": source_label(self.source),
            "trust": self.trust, "detail": self.detail,
            "as_of": self.as_of, "age": self.age,
        }


def describe(*, input: str, label: str, source: str | None, detail: str,
             as_of: datetime | str | None = None,
             now: datetime | None = None,
             trust: str | None = None) -> Provenance:
    """Build one uniform provenance entry.

    `trust` is normally derived from `source`; pass it only to override for a
    state the token alone can't express (e.g. a golden source whose bars are
    weeks stale is still `golden` by origin — the caller downgrades it)."""
    as_of_dt = None
    if isinstance(as_of, datetime):
        as_of_dt = as_of
    elif isinstance(as_of, str) and as_of:
        try:
            as_of_dt = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        except ValueError:
            as_of_dt = None
    return Provenance(
        input=input, label=label, source=source,
        trust=trust or grade(source), detail=detail,
        as_of=(as_of_dt.isoformat() if as_of_dt else
               (as_of if isinstance(as_of, str) and as_of else None)),
        age=age_text(as_of_dt, now),
    )


@dataclass
class ProvenanceBlock:
    """Every input behind one row, plus the row's worst grade.

    `worst` answers the owner's actual question in one word: is anything on
    this row leaning on a fallback, a carried number, or nothing at all?"""
    entries: list[Provenance] = field(default_factory=list)

    _ORDER = {"unavailable": 0, "carried": 1, "fallback": 2,
              "vendor": 3, "derived": 4, "golden": 5}

    @property
    def worst(self) -> str:
        if not self.entries:
            return "unavailable"
        return min((e.trust for e in self.entries),
                   key=lambda t: ProvenanceBlock._ORDER.get(t, 0))

    @property
    def summary(self) -> str:
        """One line naming what is NOT golden — silence means all-golden."""
        weak = [e for e in self.entries if e.trust in ("fallback", "carried", "unavailable")]
        if not weak:
            derived = [e for e in self.entries if e.trust == "derived"]
            if derived:
                return ("All inputs from the golden source; "
                        + ", ".join(e.label.lower() for e in derived)
                        + " computed by TradePro.")
            return "All inputs from the golden source (IBKR)."
        return "; ".join(
            f"{e.label} — {source_label(e.source)}"
            + (f", {e.age}" if e.age else "")
            for e in weak)

    def to_dict(self) -> dict:
        return {
            "worst": self.worst,
            "summary": self.summary,
            "inputs": [e.to_dict() for e in self.entries],
        }


__all__ = ["Provenance", "ProvenanceBlock", "describe", "grade",
           "source_label", "age_text", "GOLDEN_SOURCES", "FALLBACK_SOURCES"]
