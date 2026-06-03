"""Catalyst sink — Phase C-2.

Bridges the pure-Python extractor (`tradepro_strategies.catalysts`)
to the .NET registry endpoint (`POST /api/catalysts/`). Every
extracted ``Catalyst`` becomes one idempotent UPSERT — the endpoint's
ON CONFLICT on ``(symbol, kind, occurs_on, source)`` means re-running
the extractor on the same news bundle never duplicates rows. The
``source`` tag is always ``extractor.news`` so operator-added rows
stay visually distinct in the cockpit.

C-1 shipped the persistence + visibility surface; this file is the
extractor → registry plumbing. C-3 will read these rows back into
the decision row + LLM gate (the actual Ecopetrol fix).

Design notes
------------
* Stateless: `post_catalyst` + `extract_and_post` are pure functions
  with explicit ``api_base`` + ``token``; safe to call from any
  context (CLI, daemon, MCP tool).
* Confidence → severity heuristic — the extractor scores in [0, 1];
  we coarsen into the registry's three-bucket scale:
    * >= 0.9 ⇒ ``high``    (explicit dated catalyst, like
                            "FOMC May 28")
    * >= 0.6 ⇒ ``medium``  (relative-date or strong-keyword match)
    * else   ⇒ ``low``     (keyword-only, no date)
  Operators can promote / demote via the cockpit dismiss + re-add
  flow if the heuristic mis-calls.
* Posts are best-effort: a single 4xx from one row never blocks the
  next. We log a warning and continue so a single malformed item
  doesn't sink a watchlist run.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

from .catalysts import Catalyst, extract_catalysts

_log = logging.getLogger("tradepro.catalysts_sink")

# Tag every row we ingest from the news extractor with this `source`
# value. Keeps it distinguishable from operator-added rows (`operator`)
# and from future sinks (`gdelt`, `finnhub_earnings_calendar`).
EXTRACTOR_SOURCE = "extractor.news"

# Confidence thresholds — see module docstring.
HIGH_CONFIDENCE = 0.9
MEDIUM_CONFIDENCE = 0.6


def severity_from_confidence(confidence: float) -> str:
    """Coarsen the extractor's [0, 1] confidence into the registry's
    three-bucket scale. Pure function so the BDD scenarios can pin
    every boundary."""
    if confidence >= HIGH_CONFIDENCE:
        return "high"
    if confidence >= MEDIUM_CONFIDENCE:
        return "medium"
    return "low"


def catalyst_to_upsert_body(
    catalyst: Catalyst,
    *,
    symbol: str,
    source: str = EXTRACTOR_SOURCE,
) -> dict[str, Any]:
    """Build the JSON body the .NET endpoint accepts. Field names
    match the C# ``CatalystUpsertBody`` record (PascalCase keys)."""
    payload = {
        "confidence": catalyst.confidence,
        "rationale": catalyst.rationale,
        "surfaced_at": catalyst.surfaced_at,
        "link": catalyst.link,
    }
    return {
        "Symbol": symbol,
        "Kind": catalyst.kind,
        "OccursOn": catalyst.occurs_on,    # YYYY-MM-DD or None
        "Title": catalyst.title,
        "Source": source,
        "Severity": severity_from_confidence(catalyst.confidence),
        "Status": "active",
        "Payload": payload,
        "Note": None,
    }


def post_catalyst(
    catalyst: Catalyst,
    *,
    symbol: str,
    api_base: str,
    token: str,
    timeout: float = 10.0,
    source: str = EXTRACTOR_SOURCE,
) -> bool:
    """POST a single catalyst to ``/api/catalysts/``. Returns True on
    HTTP 2xx, False on anything else (logged but not raised). Best-
    effort by design — one bad row never blocks the next.

    The endpoint UPSERTs on ``(symbol, kind, occurs_on, source)`` so
    repeat calls for the same catalyst refresh the title + severity
    in place rather than duplicating."""
    body = catalyst_to_upsert_body(catalyst, symbol=symbol, source=source)
    url = api_base.rstrip("/") + "/api/catalysts/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, json=body, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        _log.warning(
            "catalyst POST failed for %s/%s: %s",
            symbol, catalyst.kind, exc,
        )
        return False
    if not (200 <= resp.status_code < 300):
        _log.warning(
            "catalyst POST %s/%s returned %d: %s",
            symbol, catalyst.kind, resp.status_code, resp.text[:200],
        )
        return False
    return True


def extract_and_post(
    symbol: str,
    news_items: list[Any],
    *,
    api_base: str,
    token: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Run the extractor over ``news_items`` and POST every detected
    catalyst. Returns a summary block the CLI prints:

        {
          "symbol": "AAPL",
          "headlines_seen": 12,
          "catalysts_extracted": 3,
          "catalysts_posted": 3,
          "catalysts_failed": 0,
          "rows": [{kind, title, occurs_on, severity, ok}, ...],
        }

    Empty inputs are a no-op (no network calls), matching the
    extractor's tolerant style."""
    catalysts = extract_catalysts(news_items)
    rows: list[dict[str, Any]] = []
    posted = 0
    failed = 0
    for c in catalysts:
        ok = post_catalyst(
            c, symbol=symbol, api_base=api_base, token=token, timeout=timeout,
        )
        rows.append({
            "kind": c.kind,
            "title": c.title,
            "occurs_on": c.occurs_on,
            "severity": severity_from_confidence(c.confidence),
            "ok": ok,
        })
        if ok:
            posted += 1
        else:
            failed += 1
    return {
        "symbol": symbol,
        "headlines_seen": len(list(news_items)) if news_items else 0,
        "catalysts_extracted": len(catalysts),
        "catalysts_posted": posted,
        "catalysts_failed": failed,
        "rows": rows,
    }
