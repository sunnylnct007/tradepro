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


def gov_contract_surge(symbols: set[str] | None = None,
                       recent_days: int = 30, baseline_days: int = 90) -> dict:
    """Federal award value in the last `recent_days` vs that name's OWN prior rate.

    A raw award total says nothing: ACN books ~$269m of federal work a quarter
    against ~$65bn of revenue — routine, not news. The only framing with any
    information is a name measured against ITSELF, which is the same
    scale-invariance rule the desk applies to momentum and volatility.

    TWO HONEST LIMITS, stated on the row rather than buried:
      - Federal awards are LUMPY. One large contract in a business that wins
        few produces a huge ratio, and that is an event, not a trend.
      - The live feed spans about five months, so the baseline is short. A
        ratio here is a description of a small window, not an estimate.

    Returns {TICKER: {...}} only for names whose recent rate exceeds their own
    baseline; everyone else is absent rather than reported as 1.0x.
    """
    rows = _get("/beta/live/govcontractsall")
    today = _dt.date.today()
    r_lo = (today - _dt.timedelta(days=recent_days)).isoformat()
    b_lo = (today - _dt.timedelta(days=recent_days + baseline_days)).isoformat()

    recent: dict = {}
    base: dict = {}
    n_recent: dict = {}
    for x in rows:
        sym = str(x.get("Ticker") or "").upper()
        if not sym or (symbols is not None and sym not in symbols):
            continue
        d = str(x.get("Date") or x.get("action_date") or "")[:10]
        try:
            amt = float(x.get("Amount") or 0)
        except (TypeError, ValueError):
            continue
        if d >= r_lo:
            recent[sym] = recent.get(sym, 0.0) + amt
            n_recent[sym] = n_recent.get(sym, 0) + 1
        elif d >= b_lo:
            base[sym] = base.get(sym, 0.0) + amt

    out: dict = {}
    months = max(baseline_days / 30.0, 1.0)
    for sym, r in recent.items():
        b = base.get(sym, 0.0) / months          # per-recent_days baseline
        if r <= 0 or b <= 0 or r <= b:
            continue
        out[sym] = {
            "recent_usd": round(r),
            "baseline_usd_per_period": round(b),
            "ratio": round(r / b, 1),
            "awards": n_recent.get(sym, 0),
            "source": "quiver_govcontracts",
            "line": (f"${r:,.0f} of federal awards in {recent_days} days across "
                     f"{n_recent.get(sym, 0)} contracts, against ${b:,.0f} per "
                     f"{recent_days} days over the prior quarter ({r / b:.1f}x). "
                     f"Awards are lumpy and this feed spans ~5 months — an "
                     f"event, not a trend. Context only."),
        }
    return out


# Selling is the DEFAULT behaviour, not a signal. Measured 15 Sep 2026 in our
# own universe over 90 days: 1,719 open-market sales against 52 purchases —
# 33x more selling — and the median sale is 1.9% of the holder's stake.
# Large-cap executives are paid in stock and diversify out of it on schedule.
# A screen that surfaced "insider selling" would light up on DELL (437
# disposals) every single day and mean nothing.
#
# So only two shapes are surfaced, and both are about CONVICTION rather than
# activity: a holder disposing of a large slice of their own stake, or several
# doing it at once. Everything else is compensation mechanics.
MEANINGFUL_SALE_FRACTION = 0.25      # p90 is 20%; only 3.5% of sales exceed 50%


def insider_sells(symbols: set[str] | None = None, within_days: int = 90) -> dict:
    """{TICKER: {...}} for sales large relative to the seller's OWN holding.

    Reports the fraction of stake disposed, never a raw count. Absent from the
    result is the normal state and means "nothing unusual", not "no selling".
    """
    rows = _get("/beta/live/insiders")
    cutoff = _dt.date.today() - _dt.timedelta(days=within_days)
    out: dict = {}
    for x in rows:
        if x.get("TransactionCode") != "S" or x.get("AcquiredDisposedCode") != "D":
            continue
        sym = str(x.get("Ticker") or "").upper()
        if not sym or (symbols is not None and sym not in symbols):
            continue
        try:
            when = _dt.date.fromisoformat(str(x["Date"])[:10])
            shares = float(x.get("Shares") or 0)
            owned_after = float(x.get("SharesOwnedFollowing") or 0)
        except Exception:  # noqa: BLE001
            continue
        if when < cutoff or shares <= 0 or (shares + owned_after) <= 0:
            continue
        frac = shares / (shares + owned_after)
        if frac < MEANINGFUL_SALE_FRACTION:
            continue                      # routine trimming — say nothing
        # DIRECT vs INDIRECT is the difference between a signal and a
        # non-event. Of 25 full-line disposals in our universe, 14 were
        # INDIRECT — DELL's are Silver Lake Technology Investors unwinding a
        # private-equity stake, which is scheduled, public and says nothing
        # about the business. A person selling their OWN directly-held shares
        # is a different fact. Reporting them as one number would fire a
        # false alarm on every fund exit.
        direct = str(x.get("directOrIndirectOwnership") or "").upper() == "D"
        rec = out.setdefault(sym, {"sales": 0, "sellers": set(), "max_fraction": 0.0,
                                   "usd": 0.0, "latest": None, "direct_sales": 0,
                                   "entity_sales": 0, "source": "quiver_insiders"})
        rec["sales"] += 1
        rec["direct_sales" if direct else "entity_sales"] += 1
        rec["sellers"].add(str(x.get("Name") or "?"))
        rec["max_fraction"] = max(rec["max_fraction"], frac)
        rec["usd"] += shares * float(x.get("PricePerShare") or 0)
        if rec["latest"] is None or when.isoformat() > rec["latest"]:
            rec["latest"] = when.isoformat()
    for sym, rec in out.items():
        names = sorted(rec.pop("sellers"))
        rec["distinct_sellers"] = len(names)
        rec["usd"] = round(rec["usd"])
        rec["max_fraction_pct"] = round(100 * rec.pop("max_fraction"), 0)
        rec["cluster"] = len(names) >= 2
        d, e = rec["direct_sales"], rec["entity_sales"]
        # A row with no DIRECT sales is a fund/trust unwind. Say that plainly
        # rather than letting it read as executives heading for the exit.
        rec["personal"] = d > 0
        who = (f"{d} directly-held" if d else "") + (" and " if d and e else "") \
              + (f"{e} via a fund or trust" if e else "")
        rec["line"] = (
            f"{rec['sales']} large insider sale{'s' if rec['sales'] != 1 else ''} "
            f"({who}) by {len(names)} "
            f"{'holders' if len(names) != 1 else 'holder'}, the largest "
            f"{rec['max_fraction_pct']:.0f}% of that line"
            + (f" (${rec['usd']:,.0f})" if rec["usd"] else "")
            + f", latest {rec['latest']}. "
            + ("" if d else "None were personally-held shares — a fund or trust "
                            "unwinding is scheduled and public. ")
            + "Insiders sell 33x more often than they buy and the median sale is "
              "1.9% of a stake, so only unusually large disposals appear. "
              "Context only."
        )
    return out
