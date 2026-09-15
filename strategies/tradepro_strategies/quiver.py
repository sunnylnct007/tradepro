"""Quiver Quantitative — alternative data, READ ONLY, context only.

Owner, 14 Sep 2026, on adding Quiver: *"start slowly in this new strategy.
may be we can have some sub strategy within it but lets see"*. So this is a
client and nothing more: it fetches, it labels, it never decides. No gate, no
lane, no alert reads from here until something has passed pre-registered
gates — the same bar the earnings work failed and the S/R study failed.

## WHAT THE DATA ACTUALLY LOOKS LIKE, measured 15 Sep 2026 against our own
## 244-name universe — because the marketing and the reality differ:

    CONGRESS       median disclosure lag 33 DAYS (p90 116). The swing rule
                   holds 20 sessions ~ 28 calendar days, so the TYPICAL
                   congressional trade becomes visible after our position
                   would already be closed. Structurally unusable at our
                   horizon; this is not an edge question.

    INSIDERS       median lag 2 days (p90 5) — genuinely fast. But of 20,000
                   recent filings only 1,837 are open-market PURCHASES (code
                   P; the rest are grants, option exercises, sales), and just
                   52 of those touch our universe — 36 of them TSM alone.
                   Large-cap executives receive stock and sell it; they do
                   not buy it. Real insider buying lives in smaller names we
                   do not trade.

    GOV CONTRACTS  47 of our names, event-driven award feed.
    WSB            63 of our names, mention count + sentiment. Wrong horizon.

So the honest use today is DISPLAY: when a name we are already looking at has
an officer or director buying with their own money, show it beside the row.
Rare, free, and never a reason on its own.
"""
from __future__ import annotations

import datetime as _dt
import logging

log = logging.getLogger("tradepro.quiver")

BASE = "https://api.quiverquant.com"
# The house convention is the tradepro/all key-value bundle, which
# get_secret() reads (env → bundle → legacy per-name → file). The standalone
# tradepro/quiver secret still holds the website login and is the documented
# home for the account; the TOKEN lives in the bundle so every runtime
# resolves it the same way everything else does.
_SECRET_KEY = "quiver-api-token"
_TOKEN: list = []          # process cache
_CACHE: dict = {}          # endpoint -> (date, payload)

# Form 4 transaction codes. Only P is an open-market purchase made with the
# filer's own money — the only one with any documented predictive record.
# A(ward), M(option exercise), F(tax withholding) and S(ale) are compensation
# mechanics and say nothing about conviction.
OPEN_MARKET_PURCHASE = "P"


def _token() -> str | None:
    if _TOKEN:
        return _TOKEN[0]
    tok = None
    try:
        from .secrets import get_secret
        raw = get_secret(_SECRET_KEY)
        tok = raw.strip() if raw else None
    except Exception as exc:  # noqa: BLE001 — absent credentials are not an error
        log.debug("quiver secret unavailable: %s", str(exc)[:80])
    _TOKEN.append(tok)
    return tok


def _get(endpoint: str, timeout: int = 30):
    """One fetch per endpoint per day. Returns [] rather than raising —
    context must never take a screen down."""
    today = _dt.date.today().isoformat()
    hit = _CACHE.get(endpoint)
    if hit and hit[0] == today:
        return hit[1]
    tok = _token()
    if not tok:
        log.info("quiver: no token configured — skipping %s", endpoint)
        return []
    try:
        import requests
        r = requests.get(f"{BASE}{endpoint}",
                         headers={"Authorization": f"Token {tok}"}, timeout=timeout)
        if r.status_code != 200:
            log.warning("quiver %s → HTTP %s", endpoint, r.status_code)
            return []
        data = r.json()
        data = data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        log.warning("quiver %s failed: %s", endpoint, str(exc)[:100])
        return []
    _CACHE[endpoint] = (today, data)
    return data


def insider_buys(symbols: set[str] | None = None, within_days: int = 90) -> dict:
    """{TICKER: {...}} for OPEN-MARKET insider purchases only.

    Deliberately narrow. A screen that showed every Form 4 would light up on
    routine option exercises and read as insider enthusiasm; the filter to
    code P is the whole point.
    """
    rows = _get("/beta/live/insiders")
    cutoff = _dt.date.today() - _dt.timedelta(days=within_days)
    out: dict = {}
    for x in rows:
        if x.get("TransactionCode") != OPEN_MARKET_PURCHASE:
            continue
        if x.get("AcquiredDisposedCode") != "A":
            continue
        sym = str(x.get("Ticker") or "").upper()
        if not sym or (symbols is not None and sym not in symbols):
            continue
        try:
            when = _dt.date.fromisoformat(str(x["Date"])[:10])
        except Exception:  # noqa: BLE001
            continue
        if when < cutoff:
            continue
        shares = float(x.get("Shares") or 0)
        price = float(x.get("PricePerShare") or 0)
        rec = out.setdefault(sym, {"buys": 0, "buyers": set(), "usd": 0.0,
                                   "latest": None, "source": "quiver_insiders"})
        rec["buys"] += 1
        rec["buyers"].add(str(x.get("Name") or "?"))
        rec["usd"] += shares * price
        if rec["latest"] is None or when.isoformat() > rec["latest"]:
            rec["latest"] = when.isoformat()
            rec["latest_title"] = (x.get("officerTitle")
                                   or ("director" if x.get("isDirector") else None)
                                   or ("10% owner" if x.get("isTenPercentOwner") else None))
    for sym, rec in out.items():
        names = sorted(rec.pop("buyers"))
        rec["distinct_buyers"] = len(names)
        rec["usd"] = round(rec["usd"], 0)
        # A CLUSTER — several insiders buying independently — is the only
        # shape with a documented prior. One buy is one person's opinion.
        rec["cluster"] = len(names) >= 2
        rec["line"] = (f"{rec['buys']} open-market insider buy"
                       f"{'s' if rec['buys'] != 1 else ''} by "
                       f"{len(names)} {'people' if len(names) != 1 else 'person'}"
                       + (f" (${rec['usd']:,.0f})" if rec["usd"] else "")
                       + f", latest {rec['latest']}"
                       + (f" — {rec['latest_title']}" if rec.get("latest_title") else "")
                       + " · context only, never a reason on its own")
    return out
