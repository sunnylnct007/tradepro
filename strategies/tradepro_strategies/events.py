"""Known, dated events between today and an option's expiry — NAMED, never missed.

OWNER BASICS 3 (SHARED_CONTEXT.md, set 2026-11-09): *"Before proposing any new
short option, check the calendar of known events between today and expiry and
NAME any that fall inside. Never miss one. The default is to avoid the event
... But an event can also be an opportunity: it is weighed, not auto-rejected.
If a trade is proposed across an event, say so plainly."* And on 10 Nov: *"this
is just one example. we can have many diff events."*

So this module answers ONE question for ONE contract window — *what is known
to happen between `start` and `end` that could move this underlying?* — and
answers it the same way for every caller: the options screen, the
post‑earnings puts board, the live‑book lane and mail, and the MCP tool. Each
of those used to carry its own private notion of "earnings in window" and
nothing else; the November 2026 election sat on every calendar in the world
and on none of ours.

TWO STORES, ONE ANSWER
  * `earnings_calendar` — the symbol's own confirmed prints (nightly Finnhub
    bulk harvest; `earnings.py` owns the authority rule).
  * `catalysts` — every other dated event. Symbol‑specific rows (an FDA
    date, an ex‑dividend) carry the ticker; MARKET‑WIDE rows (FOMC, CPI,
    jobs, elections) carry the reserved symbol ``MARKET`` and apply to
    every underlying. The seed for those lives in
    `strategies/events/market_calendar.json` and is pushed by
    `tradepro-events-sync`; the owner adds one‑offs from Settings →
    Catalysts with symbol ``MARKET``.

NEVER INFER FROM AN ABSENCE. An empty answer is only "no events" when both
stores actually answered: the earnings store was authoritative and the
catalysts API returned, and the market calendar holds at least one future
row. Anything less is `verified=False` with the gap NAMED in `gaps`, and the
screen says "could not check" rather than "clear". This is the rule that the
earnings gate already follows (the UBER case) and that the 2 Oct monitoring
incident re‑taught.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("tradepro.events")

#: Reserved catalysts symbol for events that touch every underlying.
MARKET_SYMBOL = "MARKET"

#: Where the seeded market calendar lives (repo‑committed, config not code).
MARKET_CALENDAR_PATH = Path(__file__).resolve().parents[1] / "events" / "market_calendar.json"

#: Source tag the sync CLI stamps on seeded rows, so a re‑run upserts in place
#: and an owner‑added row (source "operator") is never overwritten.
MARKET_CALENDAR_SOURCE = "market_calendar"

#: Known kinds and how they read on a board. Open list — the catalysts table
#: deliberately has no CHECK — but these are the ones with a label, an icon on
#: the frontend and a default severity. Unknown kinds still flow through,
#: labelled by their raw kind.
KINDS: dict[str, dict] = {
    "earnings":       {"label": "earnings",        "severity": "high"},
    "fomc":           {"label": "FOMC decision",   "severity": "high"},
    "central_bank":   {"label": "central bank",    "severity": "high"},
    "cpi":            {"label": "CPI",             "severity": "medium"},
    "jobs":           {"label": "jobs report",     "severity": "medium"},
    "election":       {"label": "election",        "severity": "high"},
    "ex_dividend":    {"label": "ex‑dividend",     "severity": "low"},
    "index_rebalance": {"label": "index rebalance", "severity": "low"},
    "regulatory":     {"label": "regulatory",      "severity": "medium"},
    "commodity":      {"label": "commodity",       "severity": "medium"},
    "m&a":            {"label": "M&A",             "severity": "high"},
    "operator_note":  {"label": "note",            "severity": "medium"},
}


@dataclass(frozen=True)
class Event:
    kind: str
    on: date
    title: str
    scope: str            # MARKET_SYMBOL or the ticker
    source: str
    severity: str = "medium"

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, {}).get("label", self.kind.replace("_", " "))

    def short(self) -> str:
        """`DELL earnings 26 Feb` / `FOMC decision 9 Dec` — the phrase a board
        row, a mail line and an MCP answer all use, so they cannot disagree."""
        who = "" if self.scope == MARKET_SYMBOL else f"{self.scope} "
        return f"{who}{self.label} {self.on.day} {self.on.strftime('%b')}"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "on": self.on.isoformat(),
            "title": self.title, "scope": self.scope, "source": self.source,
            "severity": self.severity, "short": self.short(),
        }


@dataclass
class EventsWindow:
    symbol: str
    start: date
    end: date
    events: list[Event] = field(default_factory=list)
    verified: bool = True
    gaps: list[str] = field(default_factory=list)

    @property
    def high(self) -> list[Event]:
        return [e for e in self.events if e.severity == "high"]

    def names(self) -> str:
        """One line for a proposal, mail or verdict. NEVER an empty string
        pretending to be clear: when the check could not be completed it says
        so, and names what was missing."""
        if self.events:
            s = " · ".join(e.short() for e in self.events)
            if not self.verified:
                s += f" — and could not check: {'; '.join(self.gaps)}"
            return s
        if not self.verified:
            return f"could not check events: {'; '.join(self.gaps)}"
        return "no known events through expiry"

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(), "end": self.end.isoformat(),
            "events": [e.to_dict() for e in self.events],
            "count": len(self.events), "high": len(self.high),
            "verified": self.verified, "gaps": list(self.gaps),
            "names": self.names(),
        }


# ── Seeded market calendar (file) ──────────────────────────────────────────

def load_market_calendar(path: Path | None = None) -> list[Event]:
    """The committed seed, validated. Raises on a malformed entry — a calendar
    that half‑loads is exactly the "never miss one" failure."""
    p = path or MARKET_CALENDAR_PATH
    raw = json.loads(p.read_text())
    out: list[Event] = []
    for i, e in enumerate(raw.get("events") or []):
        try:
            on = date.fromisoformat(e["on"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"market_calendar.json entry {i} has a bad date: {e!r}") from exc
        kind = str(e.get("kind") or "").strip()
        if not kind:
            raise ValueError(f"market_calendar.json entry {i} has no kind: {e!r}")
        title = str(e.get("title") or "").strip()
        if not title:
            raise ValueError(f"market_calendar.json entry {i} has no title: {e!r}")
        out.append(Event(
            kind=kind, on=on, title=title, scope=MARKET_SYMBOL,
            source=str(e.get("source") or MARKET_CALENDAR_SOURCE),
            severity=str(e.get("severity") or KINDS.get(kind, {}).get("severity", "medium")),
        ))
    return sorted(out, key=lambda e: e.on)


# ── The window ─────────────────────────────────────────────────────────────

Fetch = Callable[[str, dict], Optional[dict]]


def _default_fetch(api_base: str, timeout: float) -> Fetch:
    import requests

    def fetch(path: str, params: dict) -> Optional[dict]:
        try:
            r = requests.get(f"{api_base.rstrip('/')}{path}", params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            log.warning("events: %s failed: %s", path, exc)
            return None
    return fetch


#: MARKET rows per api_base, for the default (HTTP) fetch only. An 80-name
#: screen asks the same question 80 times; the answer does not change inside
#: one run. Injected fetches (tests) bypass it.
_MARKET_ROWS_CACHE: dict[str, Optional[list[dict]]] = {}


def clear_cache() -> None:
    _MARKET_ROWS_CACHE.clear()


def _catalyst_rows(fetch: Fetch, symbol: str, ahead_days: int) -> Optional[list[dict]]:
    data = fetch("/api/catalysts/", {
        "symbol": symbol, "lookbackDays": 0,
        "lookaheadDays": max(1, min(365, ahead_days)), "status": "active",
    })
    if data is None:
        return None
    return list(data.get("catalysts") or [])


def _event_from_catalyst(row: dict) -> Optional[Event]:
    on = row.get("occurs_on")
    if not on:
        return None
    try:
        d = date.fromisoformat(str(on)[:10])
    except ValueError:
        return None
    kind = str(row.get("kind") or "operator_note")
    return Event(kind=kind, on=d, title=str(row.get("title") or kind),
                 scope=str(row.get("symbol") or "").upper(),
                 source=str(row.get("source") or "catalysts"),
                 severity=str(row.get("severity") or KINDS.get(kind, {}).get("severity", "medium")))


def events_between(symbol: str, start: date, end: date, *, api_base: str,
                   fetch: Fetch | None = None, timeout: float = 10.0,
                   include_market: bool = True) -> EventsWindow:
    """Every known dated event for `symbol` (and the market) in [start, end].

    `fetch` is injectable so the logic is unit‑tested without a server. The
    earnings store is read through `earnings.py` so this module and the
    earnings gate can never disagree about what "confirmed" means.
    """
    sym = symbol.upper().strip()
    w = EventsWindow(symbol=sym, start=start, end=end)
    if end < start:
        w.verified = False
        w.gaps.append(f"window ends before it starts ({start} → {end})")
        return w
    f = fetch or _default_fetch(api_base, timeout)
    ahead = (end - date.today()).days + 1

    # 1) Earnings — the symbol's own confirmed prints.
    try:
        from .earnings import _calendar_store_events, _store_is_authoritative
        data = _calendar_store_events(sym, api_base, timeout=timeout) if fetch is None \
            else f(f"/api/earnings-calendar/{sym}", {"back": 1, "ahead": max(1, ahead)})
    except Exception as exc:  # noqa: BLE001 — the gap is recorded, not raised
        log.warning("events: earnings store read failed for %s: %s", sym, exc)
        data = None
    if not data:
        w.verified = False
        w.gaps.append("earnings store unreachable")
    else:
        try:
            authoritative = _store_is_authoritative(data.get("store"))
        except Exception:  # noqa: BLE001
            authoritative = False
        if not authoritative:
            w.verified = False
            w.gaps.append("earnings store not authoritative (empty or stale)")
        for ev in data.get("events") or []:
            try:
                d = date.fromisoformat(str(ev.get("report_date"))[:10])
            except (TypeError, ValueError):
                continue
            if start <= d <= end:
                sess = ev.get("session")
                w.events.append(Event(
                    kind="earnings", on=d, scope=sym, severity="high",
                    title=f"{sym} earnings" + (f" ({sess})" if sess else ""),
                    source=str(ev.get("source") or "earnings_calendar")))

    # 2) Symbol‑specific catalysts.
    rows = _catalyst_rows(f, sym, ahead)
    if rows is None:
        w.verified = False
        w.gaps.append("catalysts API unreachable")
    else:
        for r in rows:
            e = _event_from_catalyst(r)
            if e and start <= e.on <= end and not _dup(w.events, e):
                w.events.append(e)

    # 3) Market‑wide — FOMC, CPI, jobs, elections, …
    if include_market:
        # Ask for the whole year, not just the window: an EMPTY market calendar
        # is a gap in our data, and the only way to tell "nothing in this
        # 30‑day window" from "nobody seeded the calendar" is to look wider.
        if fetch is None and api_base in _MARKET_ROWS_CACHE:
            mrows = _MARKET_ROWS_CACHE[api_base]
        else:
            mrows = _catalyst_rows(f, MARKET_SYMBOL, 365)
            if fetch is None and mrows is not None:
                _MARKET_ROWS_CACHE[api_base] = mrows
        if mrows is None:
            if rows is not None:   # don't record the same outage twice
                w.verified = False
                w.gaps.append("catalysts API unreachable (market calendar)")
        elif not mrows:
            w.verified = False
            w.gaps.append("market calendar is EMPTY — run `tradepro-events-sync --push`")
        else:
            for r in mrows:
                e = _event_from_catalyst(r)
                if e and start <= e.on <= end and not _dup(w.events, e):
                    w.events.append(e)

    w.events.sort(key=lambda e: (e.on, 0 if e.severity == "high" else 1, e.kind))
    return w


def _dup(have: list[Event], e: Event) -> bool:
    return any(x.kind == e.kind and x.on == e.on and x.scope == e.scope for x in have)


def window_for_contract(symbol: str, expiry: str | date, *, api_base: str,
                        today: date | None = None, **kw) -> EventsWindow:
    """Convenience for callers that hold an expiry (YYYY-MM-DD or YYYYMMDD)."""
    if isinstance(expiry, str):
        s = expiry.replace("-", "")
        exp = date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    else:
        exp = expiry
    return events_between(symbol, today or date.today(), exp, api_base=api_base, **kw)


def window_for_dte(symbol: str, dte: int, *, api_base: str,
                   today: date | None = None, **kw) -> EventsWindow:
    t = today or date.today()
    return events_between(symbol, t, t + timedelta(days=max(0, int(dte))), api_base=api_base, **kw)


__all__ = [
    "Event", "EventsWindow", "KINDS", "MARKET_SYMBOL", "MARKET_CALENDAR_PATH",
    "MARKET_CALENDAR_SOURCE", "clear_cache", "events_between", "load_market_calendar",
    "window_for_contract", "window_for_dte",
]
