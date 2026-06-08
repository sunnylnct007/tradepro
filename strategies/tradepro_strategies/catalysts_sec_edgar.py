"""SEC EDGAR → catalyst registry (the free, key-less event-driven source).

GDELT (``catalysts_gdelt.py``) covers *market-wide macro* events; Finnhub
(``catalysts_finnhub.py``) covers *per-symbol* news + an earnings calendar
behind an API key. This module fills the remaining gap with the **free,
key-less SEC EDGAR full-text submissions API** — the authoritative source
for company-specific *filing* catalysts:

  * **8-K** — material events (results, M&A, executive change, bankruptcy,
    delisting, …). The single most decision-relevant filing for an
    event-driven overlay. The 8-K's ``items`` codes (e.g. ``2.02`` =
    Results of Operations) let us title the catalyst precisely and bump
    the earnings-results case to HIGH severity.
  * **10-Q / 10-K** — quarterly / annual reports (earnings + the full
    financial picture). Always HIGH — a hard, exchange-confirmed filing.
  * **Form 4** — insider transactions. Real but NOISY (every grant /
    small sale files one), so it is OFF by default; callers can opt in.

Two free endpoints, no key:

  1. ``https://www.sec.gov/files/company_tickers.json`` — the official
     ticker→CIK map. Fetched ONCE and cached in-process; CIK padded to
     the 10-digit zero-filled form the submissions API requires.

  2. ``https://data.sec.gov/submissions/CIK##########.json`` — recent
     filings for one company (the ``filings.recent`` parallel arrays:
     ``form``, ``filingDate``, ``primaryDocument``, ``accessionNumber``,
     ``items``).

CRITICAL — SEC fair-access policy:
  * Every request MUST carry a **descriptive User-Agent** identifying the
    caller (``TradePro catalyst sweep admin@tradepro.local``) or SEC
    returns ``403 Forbidden``.
  * SEC asks callers to stay under ~10 requests/second. We sleep a small
    amount between per-symbol submission fetches to stay polite (the
    daily cron has no urgency).

Best-effort + fail-open EVERYWHERE: a missing CIK, a 403, a network
error, a non-JSON body, or an unexpected shape skips that symbol (logs a
warning) and never raises. A catalyst source must never break the sweep.

No API key. No new dependencies (uses ``requests``, already a dep).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .catalysts import Catalyst

_log = logging.getLogger("tradepro.catalysts_sec_edgar")

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
_FILING_INDEX_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcompany&CIK={cik}&type={form}"
)
_TIMEOUT = 12.0
_SOURCE = "sec_edgar"

# SEC's fair-access guideline is ~10 req/sec. We don't need to be fast —
# the daily cron has no urgency — so a small pause between submission
# fetches keeps us comfortably polite (and unblocked).
_INTER_SYMBOL_DELAY = 0.15

# SEC REQUIRES a descriptive User-Agent identifying the caller, or it
# returns 403. The address is a contact, per SEC's stated policy.
_USER_AGENT = "TradePro catalyst sweep admin@tradepro.local"


# High-signal forms we ingest. Maps the raw EDGAR form string to the
# registry ``kind``. Form-4 (insider) is deliberately NOT here — it is
# opt-in via ``include_form4`` because it is extremely noisy.
_FORM_KIND = {
    "8-K": "event",
    "8-K/A": "event",
    "10-Q": "earnings",
    "10-Q/A": "earnings",
    "10-K": "earnings",
    "10-K/A": "earnings",
}

# Form 4 (insider) — opt-in only.
_FORM4 = {"4", "4/A"}

# Baseline severity per form. 10-Q / 10-K are hard exchange-confirmed
# filings → high. A generic 8-K is medium; an 8-K carrying Item 2.02
# (Results of Operations — i.e. earnings) is promoted to high below.
_FORM_SEVERITY = {
    "8-K": "medium",
    "8-K/A": "medium",
    "10-Q": "high",
    "10-Q/A": "high",
    "10-K": "high",
    "10-K/A": "high",
    "4": "low",
    "4/A": "low",
}

# Coarse confidence per severity — these are HARD, exchange-confirmed
# filing dates (unlike GDELT's surfaced-news dates), so the high bucket
# matches Finnhub's calendar-date 0.95 conviction.
_SEVERITY_CONFIDENCE = {"high": 0.95, "medium": 0.7, "low": 0.45}

# 8-K item-code → human label. The submissions API exposes the items as
# a comma-separated string of codes (e.g. "2.02,9.01"); we surface the
# most decision-relevant ones in the title. Item 2.02 = earnings results
# (promotes the 8-K to high). Codes not listed fall back to "Item X.XX".
_EIGHT_K_ITEMS = {
    "1.01": "Entry into a Material Agreement",
    "1.02": "Termination of a Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations",
    "2.03": "Creation of a Material Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting / Failure to Satisfy Listing Rule",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financials",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Appointment of Directors or Officers",
    "5.03": "Amendments to Articles / Bylaws",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# 8-K items that signal an *earnings / results* event — when present,
# promote the 8-K to high severity and re-tag its kind as earnings.
_EARNINGS_ITEMS = {"2.02"}

# 8-K items that are regulatory / governance in nature — re-tag kind as
# regulatory so the cockpit icon is correct.
_REGULATORY_ITEMS = {"3.01", "4.01", "4.02", "1.03"}


@dataclass
class SecEdgarCatalystReport:
    """Audit-friendly summary of one SEC EDGAR fetch pass for a symbol —
    mirrors ``FinnhubCatalystReport`` / ``GdeltCatalystReport`` so the
    sweep can log either uniformly."""

    symbol: str
    cik: str = ""
    filings_seen: int = 0
    catalysts_from_filings: int = 0
    all_catalysts: list[Catalyst] = field(default_factory=list)
    skipped: bool = False           # True when no CIK could be resolved
    error: str = ""


# ── HTTP helpers (fail-open) ────────────────────────────────────────────


def _http_get_json(url: str, *, timeout: float) -> Any:
    """GET ``url`` with the mandatory SEC User-Agent. Returns parsed JSON
    or None on ANY error (network, non-2xx incl. 403, bad JSON). SEC 403s
    a bare python-requests UA, so the descriptive UA is load-bearing."""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        _log.warning("SEC request failed (%s): %s", url, exc)
        return None
    if resp.status_code == 403:
        _log.warning(
            "SEC returned 403 for %s — check the User-Agent header "
            "(SEC requires a descriptive UA with a contact)", url,
        )
        return None
    if not (200 <= resp.status_code < 300):
        _log.warning("SEC returned %d for %s: %s", resp.status_code, url, resp.text[:160])
        return None
    try:
        return resp.json()
    except ValueError as exc:
        _log.warning("SEC returned non-JSON body for %s: %s", url, exc)
        return None


# ── ticker → CIK resolution (cached in-process) ─────────────────────────

# Module-level cache: {TICKER (upper): "##########" (10-digit zero-padded)}.
# None until first successful fetch; an empty dict means "fetched but
# unusable" so we don't hammer SEC on every symbol after a failure.
_CIK_CACHE: dict[str, str] | None = None


def _pad_cik(raw: Any) -> str:
    """Zero-pad a CIK (int or str) to the 10-digit form the submissions
    API requires (``CIK0000320193``). Returns "" on bad input."""
    try:
        return f"{int(str(raw).strip()):010d}"
    except (TypeError, ValueError):
        return ""


def _load_cik_map(*, timeout: float = _TIMEOUT, force: bool = False) -> dict[str, str]:
    """Fetch + cache SEC's ticker→CIK map. The map is fetched ONCE per
    process (``company_tickers.json`` is ~1MB and changes slowly). On any
    error returns {} (and caches that empty result so repeated lookups
    fail fast). Fail-open: never raises."""
    global _CIK_CACHE
    if _CIK_CACHE is not None and not force:
        return _CIK_CACHE

    data = _http_get_json(_TICKERS_URL, timeout=timeout)
    if not isinstance(data, dict):
        _log.warning("SEC ticker map fetch failed — CIK resolution unavailable")
        _CIK_CACHE = {}
        return _CIK_CACHE

    # company_tickers.json is a dict keyed by row index:
    #   {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    out: dict[str, str] = {}
    for row in data.values():
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        cik = _pad_cik(row.get("cik_str"))
        if isinstance(ticker, str) and ticker.strip() and cik:
            out[ticker.strip().upper()] = cik
    _CIK_CACHE = out
    _log.info("SEC ticker→CIK map loaded: %d tickers", len(out))
    return _CIK_CACHE


def reset_cik_cache() -> None:
    """Clear the in-process CIK cache. Test hook so each scenario starts
    from a clean slate; harmless in production."""
    global _CIK_CACHE
    _CIK_CACHE = None


def resolve_cik(symbol: str, *, timeout: float = _TIMEOUT) -> str | None:
    """Resolve a ticker to its 10-digit zero-padded CIK via the cached
    SEC map. Returns None when the symbol isn't in the map (e.g. ETFs,
    FX pairs, or anything not a US-listed filer). Fail-open."""
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    cik_map = _load_cik_map(timeout=timeout)
    return cik_map.get(symbol.strip().upper())


# ── filing → Catalyst mapping ───────────────────────────────────────────


def _parse_filing_date(raw: Any) -> str | None:
    """SEC ``filingDate`` is already ISO ``YYYY-MM-DD``. Validate + return
    it, or None when missing / unparseable."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _parse_items(raw: Any) -> list[str]:
    """The submissions API exposes 8-K items as a comma-separated string
    of codes (sometimes with a label, e.g. "2.02"). Return the list of
    clean codes; empty list when absent."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    codes: list[str] = []
    for part in raw.split(","):
        code = part.strip()
        if code:
            # Keep only the leading "X.XX" code if a label leaked in.
            codes.append(code.split()[0] if " " in code else code)
    return codes


def _accession_to_url(cik: str, accession: Any, primary_doc: Any) -> str | None:
    """Build a link to the filing on sec.gov from the accession number +
    primary document. Returns None when either is missing.

    Archives path uses the CIK without leading zeros and the accession
    number with dashes removed for the folder, dashes kept nowhere:
        https://www.sec.gov/Archives/edgar/data/<cik_int>/<accn_nodash>/<doc>
    """
    if not isinstance(accession, str) or not accession.strip():
        return None
    accn_nodash = accession.replace("-", "").strip()
    try:
        cik_int = int(cik)
    except (TypeError, ValueError):
        return None
    if isinstance(primary_doc, str) and primary_doc.strip():
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{cik_int}/{accn_nodash}/{primary_doc.strip()}"
        )
    # No primary doc — link to the filing folder index.
    return (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_int}/{accn_nodash}/"
    )


def _title_for(symbol: str, form: str, items: list[str], filing_date: str | None) -> str:
    """Compose a human title for the catalyst. For 8-Ks we surface the
    item codes + their labels (e.g. "AAPL 8-K — Item 2.02 Results of
    Operations · 2026-06-07"); for 10-Q/10-K a plain form label."""
    date_suffix = f" · {filing_date}" if filing_date else ""
    if form.startswith("8-K") and items:
        labelled = []
        for code in items[:3]:           # cap — an 8-K can list many items
            label = _EIGHT_K_ITEMS.get(code)
            labelled.append(f"Item {code} {label}" if label else f"Item {code}")
        return f"{symbol} {form} — {'; '.join(labelled)}{date_suffix}"
    form_label = {
        "10-Q": "Quarterly report (10-Q)",
        "10-K": "Annual report (10-K)",
    }.get(form, form)
    return f"{symbol} {form_label}{date_suffix}"


def _severity_and_kind(form: str, items: list[str]) -> tuple[str, str]:
    """Resolve (severity, kind) for a filing. 8-K Item 2.02 (results)
    promotes to high + earnings; regulatory items re-tag kind. Everything
    else uses the baseline form mapping."""
    base_kind = _FORM_KIND.get(form) or ("event" if form in _FORM4 else "event")
    severity = _FORM_SEVERITY.get(form, "medium")
    if form.startswith("8-K"):
        item_set = set(items)
        if item_set & _EARNINGS_ITEMS:
            return "high", "earnings"
        if item_set & _REGULATORY_ITEMS:
            return severity, "regulatory"
    return severity, base_kind


def _filing_to_catalyst(
    symbol: str,
    cik: str,
    *,
    form: str,
    filing_date_raw: Any,
    items_raw: Any,
    accession: Any,
    primary_doc: Any,
) -> Catalyst | None:
    """Map one EDGAR filing to a Catalyst. Returns None for forms we don't
    ingest or rows with no usable filing date (best-effort)."""
    filing_date = _parse_filing_date(filing_date_raw)
    if filing_date is None:
        return None
    items = _parse_items(items_raw) if form.startswith("8-K") else []
    severity, kind = _severity_and_kind(form, items)
    title = _title_for(symbol, form, items, filing_date)
    link = _accession_to_url(cik, accession, primary_doc)
    rationale = f"SEC EDGAR {form} filing for {symbol}"
    if items:
        rationale += f" (items {', '.join(items)})"
    return Catalyst(
        kind=kind,
        title=title,
        occurs_on=filing_date,
        # The filing IS the surfacing event — filing date doubles as the
        # surfaced-at (no separate "news broke" timestamp for a filing).
        surfaced_at=f"{filing_date}T00:00:00+00:00",
        confidence=_SEVERITY_CONFIDENCE.get(severity, 0.7),
        link=link,
        rationale=rationale,
    )


def _dedupe(catalysts: list[Catalyst]) -> list[Catalyst]:
    """Collapse duplicate filing catalysts. The same company can file two
    8-Ks the same day; we keep ONE catalyst per (kind, occurs_on, title) —
    highest confidence wins — and sort dated-first / soonest-first."""
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


def _in_window(filing_date: str | None, *, today: datetime, days_back: int, days_ahead: int) -> bool:
    """True when ``filing_date`` (ISO) falls within [today-days_back,
    today+days_ahead]. Undated rows are excluded (filings are always
    dated, so a missing date means a malformed row)."""
    if not filing_date:
        return False
    try:
        d = datetime.strptime(filing_date, "%Y-%m-%d").date()
    except ValueError:
        return False
    lo = (today - timedelta(days=days_back)).date()
    hi = (today + timedelta(days=days_ahead)).date()
    return lo <= d <= hi


# ── per-symbol fetch ────────────────────────────────────────────────────


def fetch_catalysts_for_symbol(
    symbol: str,
    *,
    days_back: int = 30,
    days_ahead: int = 0,
    include_form4: bool = False,
    timeout: float = _TIMEOUT,
    now: datetime | None = None,
) -> SecEdgarCatalystReport:
    """Resolve ``symbol`` → CIK, fetch its recent filings, and map the
    high-signal forms within the window to Catalyst objects.

    Fail-open: a missing CIK marks the report ``skipped``; a 403 / network
    error / bad JSON returns an empty report with ``error`` set. Never
    raises."""
    report = SecEdgarCatalystReport(symbol=symbol)
    today = now or datetime.now(timezone.utc)

    cik = resolve_cik(symbol, timeout=timeout)
    if not cik:
        # ETFs, FX, and non-US filers won't be in the map. Skip quietly.
        _log.info("SEC: no CIK for %s — skipping (not a US filer?)", symbol)
        report.skipped = True
        return report
    report.cik = cik

    data = _http_get_json(_SUBMISSIONS_URL.format(cik=cik), timeout=timeout)
    if not isinstance(data, dict):
        report.error = "submissions fetch failed"
        return report

    filings = data.get("filings")
    recent = filings.get("recent") if isinstance(filings, dict) else None
    if not isinstance(recent, dict):
        report.error = "no filings.recent block"
        return report

    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    items_arr = recent.get("items") or []
    accns = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    if not isinstance(forms, list):
        report.error = "filings.recent.form not a list"
        return report

    allowed_forms = set(_FORM_KIND)
    if include_form4:
        allowed_forms |= _FORM4

    out: list[Catalyst] = []
    for i, form in enumerate(forms):
        if not isinstance(form, str) or form not in allowed_forms:
            continue
        report.filings_seen += 1
        filing_date_raw = dates[i] if i < len(dates) else None
        parsed_date = _parse_filing_date(filing_date_raw)
        if not _in_window(parsed_date, today=today, days_back=days_back, days_ahead=days_ahead):
            continue
        c = _filing_to_catalyst(
            symbol, cik,
            form=form,
            filing_date_raw=filing_date_raw,
            items_raw=items_arr[i] if i < len(items_arr) else None,
            accession=accns[i] if i < len(accns) else None,
            primary_doc=docs[i] if i < len(docs) else None,
        )
        if c is not None:
            out.append(c)

    report.all_catalysts = _dedupe(out)
    report.catalysts_from_filings = len(report.all_catalysts)
    return report


# ── universe fetch (public entry, mirrors GDELT) ────────────────────────


def fetch_all_catalysts(
    symbols: list[str],
    *,
    days_back: int = 30,
    days_ahead: int = 0,
    include_form4: bool = False,
    timeout: float = _TIMEOUT,
    inter_symbol_delay: float = _INTER_SYMBOL_DELAY,
    log_level: int | None = None,
    now: datetime | None = None,
) -> dict[str, SecEdgarCatalystReport]:
    """Fetch SEC EDGAR filing catalysts for every symbol in ``symbols``
    and return a per-SYMBOL map of reports, ready for
    ``catalysts_sink.post_catalyst`` (post with ``source="sec_edgar"``).

    Mirrors ``catalysts_gdelt.fetch_all_catalysts`` in shape so the sweep
    can call either source uniformly. Symbols with no CIK (ETFs, FX,
    non-US filers) are skipped silently. Best-effort: a failing symbol
    contributes an empty report and never aborts the others.
    """
    if log_level is not None:
        _log.setLevel(log_level)

    by_symbol: dict[str, SecEdgarCatalystReport] = {}
    for i, sym in enumerate(symbols or []):
        if i > 0 and inter_symbol_delay > 0:
            # Stay under SEC's ~10 req/sec fair-access limit. Best-effort:
            # the daily cron has no urgency.
            time.sleep(inter_symbol_delay)
        try:
            report = fetch_catalysts_for_symbol(
                sym,
                days_back=days_back,
                days_ahead=days_ahead,
                include_form4=include_form4,
                timeout=timeout,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001 — never let one symbol break the sweep
            _log.warning("SEC symbol %s failed unexpectedly: %s", sym, exc)
            report = SecEdgarCatalystReport(symbol=sym, error=str(exc))
        by_symbol[sym] = report
        if report.catalysts_from_filings:
            _log.info(
                "SEC %s (CIK %s): %d filings → %d catalysts",
                sym, report.cik, report.filings_seen, report.catalysts_from_filings,
            )
    return by_symbol


__all__ = [
    "SecEdgarCatalystReport",
    "fetch_all_catalysts",
    "fetch_catalysts_for_symbol",
    "resolve_cik",
    "reset_cik_cache",
]
