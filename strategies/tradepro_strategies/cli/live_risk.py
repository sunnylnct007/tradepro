"""The live book, read for risk — what to close and what you are carrying.

Owner, 7 Nov 2026: "for live account we wree in deep trouble and tradepro
didnt asissted at all". That is accurate, and the reason is not that TradePro
could not work it out. On 1 Nov the same rules this lane uses flagged MRVL
Nov20 230P and CRWV Nov20 70P as CLOSE on assignment grounds, 19 days out.
The analysis existed in a conversation and never on a screen, so when the week
turned there was nothing to open.

WHAT IT REPORTS

  per position   the close verdict from quant_engine.options.manage — the
                 SAME rules, not a second copy — with the number that fired it
  concentration  capital by sector, because the week's damage was a theme and
                 not a name: SNDK -£47,000, MU -£20,260, STX -£13,413, all
                 memory and storage
  assignment     what the short puts would oblige you to buy

READ-ONLY, BY CONSTRUCTION. The source is the IBKR Flex Web Service — a report
pulled with its own token which cannot place, modify or cancel anything. This
lane could not trade the live account if it tried.

AND IT DOES NOT ADVISE. It states what the position is against rules the owner
can read and change. "CLOSE" here means "this crossed a line you set", never
"I think you should sell".
"""
from __future__ import annotations

from typing import Callable

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from ..quant_engine.options.manage import ManageRules, assess, parse_occ
from .push_to_api import load_credentials

log = logging.getLogger("tradepro.live_risk")

ARTIFACT_UNIVERSE = "live-book-risk"


def redact_flex(text: str, token: str | None) -> str:
    """Strip a Flex token (and any t=/q= query value) out of an error string."""
    import re as _re
    if token:
        text = text.replace(token, "***")
    return _re.sub(r"([?&][tq]=)[^&\s)]+", r"\1***", text)


def _flex_direct(token: str, query_id: str) -> list[dict]:
    """Pull the Open Positions report straight from IBKR.

    WHY THIS READ EXISTS ON THE MAC. From 7 Nov 2026 the deployed API's Flex
    reads came back "Error 403 - Access Denied" while this machine got
    Status=Success, and that was read as IBKR blocking the cloud range. It was
    not: measured 20 Nov 2026, a request with NO User-Agent gets exactly that
    403 from the Mac too, and the API's Flex client sent none (requests always
    does). The API client now sends one, so the API path works again; this
    direct read stays as the second route until live-risk moves off the Mac.

    Two-step, as IBKR defines it: SendRequest returns a reference code, then
    GetStatement is polled until the report exists. The second call goes to a
    DIFFERENT HOST — the one named in the first response — which is easy to
    miss and silently returns nothing if you reuse the first.

    SUMMARY ROWS ONLY. An Open Positions report carries a SUMMARY row per
    holding AND a LOT row per tax lot; counting both doubles the book. Measured
    1 Nov 2026: 92 elements, 30 SUMMARY, 62 LOT, for 30 real positions, with
    SNDK reading 240 shares against an actual 120. Rows with no levelOfDetail
    are KEPT — older reports omit it and dropping them would silently empty the
    book rather than fail loudly.
    """
    import time
    import xml.etree.ElementTree as ET

    send = ("https://ndcdyn.interactivebrokers.com/AccountManagement/"
            f"FlexWebService/SendRequest?t={token}&q={query_id}&v=3")
    r = requests.get(send, timeout=(5, 60))
    root = ET.fromstring(r.text)
    ref = (root.findtext("ReferenceCode") or "").strip()
    if not ref:
        raise RuntimeError(
            f"SendRequest gave no reference code. IBKR said: "
            f"{r.text.strip()[:200]}")
    url = (root.findtext("Url")
           or "https://gdcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement")

    # IBKR GENERATES the report after the request; polling is not optional and
    # "not ready yet" comes back as a normal body, not an error.
    deadline = time.monotonic() + 120
    body = ""
    while time.monotonic() < deadline:
        body = requests.get(f"{url}?q={ref}&t={token}&v=3", timeout=(5, 120)).text
        if "<OpenPosition" in body or "FlexQueryResponse" in body:
            break
        time.sleep(4)

    out: list[dict] = []
    for e in ET.fromstring(body).iter("OpenPosition"):
        if (e.get("levelOfDetail") or "").upper() == "LOT":
            continue
        out.append({
            "symbol": e.get("symbol"),
            "assetCategory": e.get("assetCategory"),
            "quantity": _f(e.get("position")),
            "costBasisPrice": _f(e.get("costBasisPrice")),
            "markPrice": _f(e.get("markPrice")),
            "unrealizedPnl": _f(e.get("fifoPnlUnrealized")),
            "currency": e.get("currency"),
            "conid": e.get("conid"),
        })
    return out


def _spots(symbols: set[str]) -> dict[str, float | None]:
    """Last close per underlying. Flex marks the OPTION but not the stock, and
    moneyness is the whole basis of the assignment rule."""
    from ..ibkr_bars import fetch_daily_bars_with_provenance
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=90)
    out: dict[str, float | None] = {}
    for s in sorted(symbols):
        try:
            df, _ = fetch_daily_bars_with_provenance(
                s, start, end, asset_class="us_etf", fetched_by="live-risk")
            col = "adj_close" if "adj_close" in df.columns else "close"
            out[s] = float(df[col].dropna().iloc[-1])
        except Exception as exc:  # noqa: BLE001 — a missing spot is UNKNOWN, not a pass
            log.warning("%s: no spot (%s)", s, str(exc)[:70])
            out[s] = None
    return out


def _sector_map() -> dict[str, str]:
    """symbol -> sector from the derived universe. Absent names fall to
    "unclassified", which GROUPS them rather than exempting them — an
    unclassified name must not be the way round a concentration limit."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "universe" / "wheel_sleeve.json"
    try:
        d = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}
    return {r["symbol"]: (r.get("sector") or "unclassified")
            for r in (d.get("detail") or []) if r.get("symbol")}


def build(positions: list[dict], *, rules: ManageRules | None = None,
          today: date | None = None, nav_usd: float | None = None,
          events_for: "Callable[[str, str], dict] | None" = None) -> dict:
    """Positions -> the risk picture. Pure, so it is testable without a broker.

    `events_for(symbol, expiry)` -> the known-events window dict (events.py)
    for one contract. Injected rather than imported so the builder stays pure;
    `main` passes the real one. None = events were not checked, and the rows
    say so rather than showing an empty list."""
    today = today or date.today()

    def _ev(sym: str, expiry: str) -> dict:
        if events_for is None:
            return {"events": [], "count": 0, "high": 0, "verified": False,
                    "gaps": ["events not checked in this run"],
                    "names": "events not checked in this run"}
        try:
            return events_for(sym, expiry)
        except Exception as exc:  # noqa: BLE001 — carried, never raised
            return {"events": [], "count": 0, "high": 0, "verified": False,
                    "gaps": [f"events check failed: {str(exc)[:120]}"],
                    "names": f"could not check events: {str(exc)[:120]}"}
    opts, stocks = [], []
    for p in positions:
        occ = parse_occ(str(p.get("symbol") or ""))
        (opts if occ else stocks).append((occ, p))

    shorts = [(occ, p) for occ, p in opts
              if occ and occ[2] == "P" and (p.get("quantity") or 0) < 0]
    # LONG PUTS PAIR WITH SHORT PUTS (24 Nov 2026). The book now holds put
    # credit spreads and bought put-spread hedges; a lane that counts every
    # short put as a naked obligation overstates the risk of defined-risk
    # legs several times over. Same root + expiry: a long strike BELOW the
    # short = credit spread (risk = width); a long strike ABOVE = the short is
    # the financing leg of a bought hedge (no obligation of its own).
    longs = {}
    for occ, p in opts:
        if occ and occ[2] == "P" and (p.get("quantity") or 0) > 0:
            longs.setdefault((occ[0], occ[1]), []).append([occ[3], float(p.get("quantity") or 0), p])

    def _pair(root: str, expiry: str, strike: float, qty: float):
        """Consume long-put quantity for this short leg; returns
        (structure, long_strike, protected_qty)."""
        cands = longs.get((root, expiry)) or []
        for leg in sorted(cands, key=lambda l: abs(l[0] - strike)):
            if leg[1] <= 0:
                continue
            take = min(leg[1], qty)
            leg[1] -= take
            return ("credit_spread" if leg[0] < strike else "hedge_short_leg"), leg[0], take
        return "naked", None, 0.0
    # SHORT CALLS TOO. Owner, 9 Nov: the screen "only shows the put position
    # and not the equity position". The calls are the repair trade on the
    # shares (ORCL 145C against ORCL stock) and the thing that caps the
    # recovery; a book view without them is not the book.
    held_qty = {str(p.get("symbol") or "").upper(): float(p.get("quantity") or 0)
                for _o, p in stocks}
    short_calls = [(occ, p) for occ, p in opts
                   if occ and occ[2] == "C" and (p.get("quantity") or 0) < 0]
    calls = []
    for occ, p in short_calls:
        root, expiry, _r, strike = occ
        q = abs(float(p.get("quantity") or 0))
        calls.append({
            "symbol": root, "contract": p.get("symbol"), "expiry": expiry,
            "strike": strike, "qty": -q,
            "dte": (date.fromisoformat(expiry) - today).days,
            "credit": _f(p.get("costBasisPrice")), "mark": _f(p.get("markPrice")),
            "unrealised": _f(p.get("unrealizedPnl")),
            # covered = shares on hand for the contracts sold (100/contract)
            "covered": int(min(held_qty.get(root, 0.0), q * 100)) or None,
            "events": _ev(root, expiry),
        })
    spot = _spots({occ[0] for occ, _ in shorts})

    rows, assignment = [], 0.0
    for occ, p in shorts:
        root, expiry, _right, strike = occ
        qty = abs(float(p.get("quantity") or 0))
        dte = (date.fromisoformat(expiry) - today).days
        v = assess(strike=strike, spot=spot.get(root),
                   credit=_f(p.get("costBasisPrice")), mark=_f(p.get("markPrice")),
                   dte=dte, rules=rules)
        structure, long_strike, protected = _pair(root, expiry, strike, qty)
        if structure == "credit_spread":
            obligation = (strike - long_strike) * 100 * protected + strike * 100 * (qty - protected)
        elif structure == "hedge_short_leg":
            obligation = strike * 100 * (qty - protected)
        else:
            obligation = strike * 100 * qty
        assignment += obligation
        if structure != "naked":
            # The naked-put management rules (basis shortfall, the 10% line) do
            # not apply to a defined-risk leg; the income monitor manages it.
            import dataclasses
            v = dataclasses.replace(
                v, action="HOLD", urgency=0,
                rule="defined-risk spread" if structure == "credit_spread" else "hedge leg",
                why=(f"short {strike:g}P paired with long {long_strike:g}P ×{protected:g}: "
                     + ("max loss is the width, managed by the income monitor (take profit 50%, swing-low exit)"
                        if structure == "credit_spread" else
                        "the financing leg of a bought put spread — the long leg above it carries the protection")))
        rows.append({
            "symbol": root, "contract": p.get("symbol"), "expiry": expiry,
            "strike": strike, "qty": -qty, "dte": dte,
            "structure": structure, "long_strike": long_strike, "protected_qty": protected,
            "obligation_usd": round(obligation, 2),
            "spot": spot.get(root), "credit": _f(p.get("costBasisPrice")),
            "mark": _f(p.get("markPrice")),
            "unrealised": _f(p.get("unrealizedPnl")),
            "action": v.action, "rule": v.rule, "why": v.why, "urgency": v.urgency,
            # The arithmetic behind the verdict, carried rather than re-derived.
            # `hold` is the position at expiry IF THE STOCK DOES NOT MOVE —
            # the honest counterfactual to closing, and the number the screen
            # was missing when it showed a mark three times worse than the
            # outcome it implied.
            "basis": v.effective_basis, "cushion_pct": v.basis_cushion_pct,
            "close_pnl": v.close_pnl, "hold_pnl": v.hold_pnl,
            "forfeit": v.forfeit,
            # Known dated events between today and this expiry, NAMED (OWNER
            # BASICS 3). The election week is the reason this field exists.
            "events": _ev(root, expiry),
        })
    # Worst first: a board read top-down must start with what forces a
    # decision soonest, then by how little time is left to make it.
    rows.sort(key=lambda r: (-r["urgency"], r["dte"] if r["dte"] is not None else 9999))

    # THE CONTENT NESTS UNDER "artifact". The ingest endpoint takes universe,
    # label and uploaded_by at the top level and REQUIRES everything else
    # inside an `artifact` object — a flat payload is rejected with a bare 400,
    # which is what the first run of this lane got after reading the book
    # perfectly. The data was right and the envelope was wrong.
    return {
        "universe": ARTIFACT_UNIVERSE,
        "label": "latest",
        "uploaded_by": "tradepro-live-risk",
        "artifact": {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        # The Flex report is END-OF-DAY: this is the session the positions
        # describe, which is what "fresh" means for this card (24 Nov 2026).
        "report_date": max((str(p.get("reportDate") or "")[:10] for p in positions), default=None) or None,
        "counts": {
            "short_puts": len(rows),
            "spreads": sum(1 for r in rows if r.get("structure") == "credit_spread"),
            "hedge_legs": sum(1 for r in rows if r.get("structure") == "hedge_short_leg"),
            "close": sum(1 for r in rows if r["action"] == "CLOSE"),
            "watch": sum(1 for r in rows if r["action"] == "WATCH"),
            "hold": sum(1 for r in rows if r["action"] == "HOLD"),
            "unknown": sum(1 for r in rows if r["action"] == "UNKNOWN"),
        },
        "assignment_obligation_usd": round(assignment, 2),
        "unrealised_usd": round(sum(r["unrealised"] or 0 for r in rows), 2),
        # BOTH TOTALS, SIDE BY SIDE, because reporting only the first one is
        # what made this book look three times worse than it is. The mark is
        # the cost of exiting today; the difference between the two is time
        # value the seller has already earned and has not yet been credited.
        "unrealised_hold_usd": round(
            sum(r["hold_pnl"] or 0 for r in rows), 2),
        "time_value_at_stake_usd": round(
            sum(r["forfeit"] or 0 for r in rows), 2),
        "rules": {"take_profit_pct": (rules or ManageRules()).take_profit_pct,
                  "near_expiry_days": (rules or ManageRules()).near_expiry_days,
                  "max_basis_shortfall_pct":
                      (rules or ManageRules()).max_basis_shortfall_pct},
        "exposures": exposures(rows, [
            {"symbol": p.get("symbol"), "qty": p.get("quantity")} for _o, p in stocks],
            sectors=_sector_map(), nav_usd=nav_usd),
        "put_limits": put_limits(rows, nav_usd=nav_usd, sectors=_sector_map()),
        "nav_usd": nav_usd,
        "positions": rows,
        "calls": calls,
        # Every distinct event any contract in the book runs across, soonest
        # first — the one list to read before the week starts.
        "events": _book_events(rows + calls),
        "equity": [{"symbol": p.get("symbol"), "qty": p.get("quantity"),
                    "unrealised": _f(p.get("unrealizedPnl")),
                    "covered_call": next((f"{c['strike']:.0f}C {c['expiry']}" for c in calls
                                          if c["symbol"] == str(p.get("symbol") or "").upper()), None)}
                   for _o, p in stocks],
        },
    }


def _book_events(contracts: list[dict]) -> dict:
    seen: dict[tuple, dict] = {}
    gaps: set[str] = set()
    verified = True
    for c in contracts:
        w = c.get("events") or {}
        if not w.get("verified"):
            verified = False
            gaps.update(w.get("gaps") or [])
        for e in w.get("events") or []:
            key = (e.get("on"), e.get("kind"), e.get("scope"))
            seen.setdefault(key, e)
    evs = sorted(seen.values(), key=lambda e: (e.get("on") or "", e.get("kind") or ""))
    return {"events": evs, "count": len(evs), "verified": verified, "gaps": sorted(gaps)}


# ── exposure, which is the part that was avoidable ────────────────────────
#
# Owner, 8 Nov 2026: "a major drawdown like this cannot be predicted buyt
# ovrall exposures could have been avoided". Exactly right, and the per-position
# verdicts cannot see any of it — on 1 Nov every one of the 22 puts was inside
# its own limits and the BOOK was a single bet:
#
#   * 22 short puts, essentially all tech and semiconductors
#   * GOOGL held as 200 shares AND three short puts — one position in four
#     costumes, every leg losing together
#   * $619,000 of assignment obligation against roughly $218,000 of NAV
#
# None of that is a forecast. All of it was countable that morning.

#: No single sector may carry more than this share of the assignment
#: obligation. ~7 investable sectors means equal weight is about 14%; 35% is
#: 2.5x that — permissive, because concentration is often where the premium
#: is, and still far below the ~100% tech the live book actually ran.
# OWNER BASICS 5 (SHARED_CONTEXT, set 2026-11-09): "exposure to a single sector
# must not exceed 40-45% of the book". 0.45 is the line; 0.35 was my number,
# written before he set one. One definition, read by the card and the mail.
MAX_SECTOR_SHARE = float(os.environ.get("TRADEPRO_LIVE_MAX_SECTOR_SHARE", "0.45"))
# OWNER BASICS 6: "keep some portion of the book in cash ... about $50K", and
# "a short put should be backed by cash that can take the assignment". An
# indication, not an exact limit — reported as a finding, never a refusal.
CASH_RESERVE_USD = float(os.environ.get("TRADEPRO_LIVE_CASH_RESERVE_USD", "50000"))

#: Assignment obligation as a multiple of account value. At 1.0x every short
#: put could be exercised and paid for. The live book was at 2.8x.
MAX_ASSIGNMENT_X_NAV = float(os.environ.get("TRADEPRO_LIVE_MAX_ASSIGNMENT_X_NAV", "1.0"))


#: The owner's short-put limits (build pack, 13 Nov 2026, section 2): total
#: strike value at most 50% of net liquidation, 20% per sector, 10% per name.
PUT_LIMIT_TOTAL = float(os.environ.get("TRADEPRO_PUT_LIMIT_TOTAL_X_NAV", "0.50"))
PUT_LIMIT_SECTOR = float(os.environ.get("TRADEPRO_PUT_LIMIT_SECTOR_X_NAV", "0.20"))
PUT_LIMIT_NAME = float(os.environ.get("TRADEPRO_PUT_LIMIT_NAME_X_NAV", "0.10"))


def put_limits(rows: list[dict], *, nav_usd: float | None,
               sectors: dict[str, str] | None = None) -> dict:
    """Is the book inside the owner's short-put limits, and may NEW puts be
    sold? One answer every screen reads. Without an account value the answer
    is "unknown", and screens treat unknown as no new puts."""
    sectors = sectors or {}
    total = sum(_obligation(r) for r in rows)
    if not nav_usd:
        return {"status": "unknown", "new_puts_allowed": False, "strike_value_usd": round(total),
                "detail": "account value not supplied, so the limit cannot be checked — "
                          "treat as no new puts"}
    by_sector: dict[str, float] = defaultdict(float)
    by_name: dict[str, float] = defaultdict(float)
    for r in rows:
        ob = _obligation(r)
        by_sector[sectors.get(r.get("symbol")) or "unclassified"] += ob
        by_name[str(r.get("symbol") or "").upper()] += ob
    breaches = []
    if total > PUT_LIMIT_TOTAL * nav_usd:
        breaches.append(f"total strike value ${total:,.0f} is {total / nav_usd:.0%} of "
                        f"${nav_usd:,.0f} account value (limit {PUT_LIMIT_TOTAL:.0%})")
    for sec, ob in sorted(by_sector.items(), key=lambda kv: -kv[1]):
        if ob > PUT_LIMIT_SECTOR * nav_usd:
            breaches.append(f"{sec} ${ob:,.0f} is {ob / nav_usd:.0%} (limit {PUT_LIMIT_SECTOR:.0%})")
    for name, ob in sorted(by_name.items(), key=lambda kv: -kv[1]):
        if ob > PUT_LIMIT_NAME * nav_usd:
            breaches.append(f"{name} ${ob:,.0f} is {ob / nav_usd:.0%} (limit {PUT_LIMIT_NAME:.0%})")
    return {"status": "breached" if breaches else "within", "new_puts_allowed": not breaches,
            "strike_value_usd": round(total), "nav_usd": round(nav_usd),
            "limits": {"total": PUT_LIMIT_TOTAL, "sector": PUT_LIMIT_SECTOR, "name": PUT_LIMIT_NAME},
            "breaches": breaches,
            "detail": ("No new puts until the book is back within limits: " + "; ".join(breaches[:3])
                       if breaches else "inside every short-put limit")}


def exposures(rows: list[dict], equity: list[dict], *,
              sectors: dict[str, str] | None = None,
              nav_usd: float | None = None) -> list[dict]:
    """Book-level findings. Returns [] when the book is within its limits.

    Each finding carries the NUMBER, because "too concentrated" is an opinion
    and "73% of your assignment obligation is one sector" is a fact the owner
    can act on or overrule.
    """
    sectors = sectors or {}
    out: list[dict] = []
    total = sum(_obligation(r) for r in rows)

    # 1. ONE THEME. The week's damage was a sector, not a name.
    by_sector: dict[str, float] = defaultdict(float)
    for r in rows:
        ob = _obligation(r)
        by_sector[sectors.get(r.get("symbol")) or "unclassified"] += ob
    for sec, ob in sorted(by_sector.items(), key=lambda kv: -kv[1]):
        if total and ob / total > MAX_SECTOR_SHARE:
            out.append({
                "kind": "concentration", "severity": 2, "subject": sec,
                "detail": f"{sec} is {ob/total:.0%} of your assignment obligation "
                          f"(${ob:,.0f} of ${total:,.0f}), past the "
                          f"{MAX_SECTOR_SHARE:.0%} line. A move against this "
                          f"sector hits every one of these at once.",
            })

    # 2. THE SAME BET TWICE. Long stock plus a short put on one name is not
    #    two positions — both legs lose together, and the screen showed them
    #    as separate rows with separate verdicts.
    held = {str(e.get("symbol") or "").upper(): e for e in equity
            if (e.get("qty") or 0) > 0}
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if sym in held:
            out.append({
                "kind": "doubled", "severity": 2, "subject": sym,
                "detail": f"you hold {held[sym].get('qty'):g} shares of {sym} AND "
                          f"a short {r.get('strike')}P. That is ONE bet, not two "
                          f"— both legs lose together, and assignment adds more "
                          f"of what you already own.",
            })

    # 3. COULD YOU PAY FOR IT. Not a forecast — arithmetic.
    if nav_usd and total:
        x = total / nav_usd
        if x > MAX_ASSIGNMENT_X_NAV:
            out.append({
                "kind": "assignment", "severity": 2, "subject": "book",
                "detail": f"assignment obligation ${total:,.0f} is {x:.1f}x your "
                          f"${nav_usd:,.0f} account. If these were all exercised "
                          f"you could not pay for them without selling something.",
            })
    elif total:
        out.append({
            "kind": "assignment", "severity": 1, "subject": "book",
            "detail": f"assignment obligation ${total:,.0f} — account value not "
                      f"supplied, so this cannot be checked against what you "
                      f"could actually fund.",
        })
    return out


def _obligation(r: dict) -> float:
    """What a short-put row can be made to buy: the strike for a naked put,
    the width for a credit spread, nothing for a hedge's financing leg."""
    if r.get("obligation_usd") is not None:
        return float(r["obligation_usd"])
    return (r.get("strike") or 0) * 100 * abs(r.get("qty") or 0)


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None



def _alert_text(art: dict) -> str:
    """What the mail says. The SAME verdicts and numbers the screen shows —
    built from the published artifact rather than recomputed, so a mail can
    never disagree with the board it is alerting about."""
    c = art["counts"]
    rows = art.get("positions") or []
    act = [r for r in rows if r.get("action") == "CLOSE"]
    lines = [
        f"{c['short_puts']} short puts · {c['close']} to close · "
        f"{c['watch']} watch · {c['hold']} hold",
        "",
        f"Assignment obligation ${art['assignment_obligation_usd']:,.0f} "
        f"if every short put is exercised.",
    ]
    # BOTH TOTALS. Reporting the mark alone is what made this book read three
    # times worse than it is, and a mail that repeats that mistake is worse
    # than the screen because it arrives unprompted.
    if art.get("unrealised_hold_usd") is not None:
        lines += [
            f"Marked at ${art['unrealised_usd']:,.0f}; at expiry with prices "
            f"unchanged it is ${art['unrealised_hold_usd']:,.0f}. The "
            f"${art.get('time_value_at_stake_usd') or 0:,.0f} between them is "
            f"time value you forfeit by closing early.",
        ]
    for e in (art.get("exposures") or []):
        lines += ["", f"EXPOSURE — {e.get('detail')}"]
    # KNOWN EVENTS the book runs across. Named, every time (OWNER BASICS 3);
    # when the check could not complete the mail says that, in the same place.
    be = art.get("events") or {}
    if be.get("events"):
        lines += ["", "KNOWN EVENTS BEFORE YOUR EXPIRIES", "---------------------------------"]
        for e in be["events"]:
            lines.append(f"  {e.get('on')}  {e.get('short')}")
    if not be.get("verified", True):
        lines += ["", "Events calendar NOT fully checked: " + "; ".join(be.get("gaps") or [])]
    if act:
        lines += ["", "TO CLOSE", "--------"]
        for r in act:
            lines.append(
                f"  {r['symbol']} {r['strike']:.0f}P {r['expiry']} "
                f"({r['dte']}d) — {r['why']}")
            ev = (r.get("events") or {}).get("events") or []
            if ev:
                lines.append("      events before expiry: " + " · ".join(e["short"] for e in ev))
    else:
        lines += ["", "Nothing crosses a close line today."]
    lines += ["", "TradePro reads this account and never places an order into "
              "it. Algo execution is paper only."]
    return "\n".join(lines)


def _mail(art: dict) -> int:
    """Send the live alert, or say why it could not be sent.

    A FAILED MAIL IS REPORTED, NEVER SWALLOWED. The whole point of an alert is
    that silence means "nothing to tell you"; a mailer that fails quietly turns
    silence into "we could not reach you", which reads identically.
    """
    try:
        from ..email_digest import LIVE, labelled
        from .email_digest import resolve_smtp_creds, send_email
    except Exception as exc:  # noqa: BLE001
        print(f"email machinery unavailable: {exc}", file=sys.stderr)
        return 1
    c = art["counts"]
    subject = (f"{c['close']} to close, {c['watch']} watch "
               f"— ${art['assignment_obligation_usd']:,.0f} obligation")
    try:
        cfg = resolve_smtp_creds()
        send_email(labelled(LIVE, subject=subject, text_body=_alert_text(art)), cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"ALERT NOT SENT — {exc}", file=sys.stderr)
        return 1
    print(f"alert mailed: [LIVE] {subject}")
    return 0


FLEX_SECRET_ID = "tradepro/ibkr-flex"      # the same secret the API loads


def flex_credentials(env: "dict | None" = None,
                     fetch: "callable | None" = None) -> tuple[str, str, str]:
    """(token, positions_query_id, source). Env wins; Secrets Manager next.

    Owner, 9 Nov 2026: "on the flex token i had already added before. why
    cant u get from aws secret". He had — in tradepro/ibkr-flex, which the
    API reads at startup. This lane asked for a shell export instead, so the
    live card sat 46 hours stale waiting for a human to retype a credential
    the system already held. Now the lane reads the same secret the API does.

    `env` and `fetch` are injectable so the precedence is testable without
    AWS. Nothing here logs or returns the secret to a caller that prints it.
    """
    e = os.environ if env is None else env
    t = (e.get("TRADEPRO_IBKR_FLEX_TOKEN") or "").strip()
    q = (e.get("TRADEPRO_IBKR_FLEX_POSITIONS_QUERY") or "").strip()
    if t and q:
        return t, q, "environment"
    if fetch is None:
        from ..secrets import get_json_secret as fetch  # type: ignore[assignment]
    d = fetch(FLEX_SECRET_ID) or {}
    t2 = str(d.get("token") or t).strip()
    q2 = str(d.get("positions_query_id") or q).strip()
    if t2 and q2:
        return t2, q2, f"secrets manager ({FLEX_SECRET_ID})"
    return t2, q2, ""


def _exposure_for(positions: list[dict], base: str, token: str | None, *,
                  nav_usd: float | None) -> dict:
    """Dollar-delta by segment for the live book. Deltas from the API's IBKR
    chain route per (root, expiry, right); Black-Scholes from the option's
    mark when the chain has none (labelled); a leg with neither is a named
    gap. Segment map and cap from settings-kv `risk_guardrails`."""
    from ..income_spreads import _kv_get
    from ..live_exposure import ExposureConfig, exposure, legs_from_positions
    from ..quant_engine.options.manage import parse_occ

    try:
        kv = _kv_get(base, token or "", "risk_guardrails") or {}
        if isinstance(kv, dict) and "value" in kv and isinstance(kv["value"], (dict, str)):
            kv = kv["value"]
        if isinstance(kv, str):
            kv = json.loads(kv)
    except Exception as exc:  # noqa: BLE001
        log.warning("risk_guardrails not readable (%s) — sector map only", str(exc)[:80])
        kv = {}
    cfg = ExposureConfig.from_kv(kv if isinstance(kv, dict) else {}, sector_of=_sector_map())

    roots = set()
    for p in positions:
        occ = parse_occ(str(p.get("symbol") or ""))
        roots.add(occ[0] if occ else str(p.get("symbol") or "").upper())
    spots = _spots({r for r in roots if r})

    H = {"Authorization": f"Bearer {token}"} if token else {}
    chain_cache: dict[tuple, dict[float, float]] = {}

    def greeks_for(root: str, expiry: str, right: str, strike: float) -> dict | None:
        key = (root, expiry, right)
        if key not in chain_cache:
            table: dict[float, dict] = {}
            try:
                r = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{root}",
                                 params={"expiry": expiry.replace("-", ""), "right": right, "maxStrikes": 60},
                                 headers=H, timeout=(5, 90))
                if r.status_code == 200:
                    for leg in (r.json().get("legs") or []):
                        if str(leg.get("right") or "").upper().startswith(right) and leg.get("delta") is not None:
                            iv = leg.get("impliedVolPct")
                            table[float(leg["strike"])] = {"delta": float(leg["delta"]),
                                                           "gamma": (float(leg["gamma"]) if leg.get("gamma") is not None else None),
                                                           "iv": (float(iv) / 100.0 if iv is not None else None)}
            except Exception as exc:  # noqa: BLE001 — the mark-solved greeks are the fallback
                log.info("%s %s %s: chain greeks unavailable (%s)", root, expiry, right, str(exc)[:60])
            chain_cache[key] = table
        return chain_cache[key].get(float(strike))

    legs = legs_from_positions(positions, spots=spots, delta_for=None, greeks_for=greeks_for, cfg=cfg)
    gbp = None
    try:
        fx = _kv_get(base, token or "", "income_engine") or {}
        fx = fx.get("value", fx) if isinstance(fx, dict) else fx
        if isinstance(fx, dict) and fx.get("fx_gbp_per_usd_fallback"):
            gbp = float(fx["fx_gbp_per_usd_fallback"])
    except Exception:  # noqa: BLE001
        gbp = None
    out = exposure(legs, cfg=cfg, nav_usd=nav_usd, gbp_per_usd=gbp)
    out["segment_map_source"] = "settings-kv risk_guardrails" if cfg.segments else "sector map (no risk_guardrails.segments)"
    return out


def _put_book_for(rows: list[dict], positions: list[dict], base: str, token: str | None, *,
                  nav_usd: float | None) -> dict:
    """The owner's put policy on the live rows. Technicals (10/20-day lows,
    20-day average) from the golden daily store; the sector ETF's day from the
    same store; flags and cash figures from settings-kv `risk_guardrails`."""
    from ..income_spreads import _kv_get
    from ..put_book import put_book

    try:
        kv = _kv_get(base, token or "", "risk_guardrails") or {}
        kv = kv.get("value", kv) if isinstance(kv, dict) else kv
        if isinstance(kv, str):
            kv = json.loads(kv)
    except Exception as exc:  # noqa: BLE001
        log.warning("risk_guardrails not readable for the put book (%s)", str(exc)[:80])
        kv = {}

    def bars_for(sym: str) -> dict | None:
        from ..daily_bars import load_bars
        df = load_bars(sym, "1d", lookback_days=60)
        if df is None or len(df) < 21:
            return None
        c = df["close"].astype(float)
        lo = df["low"].astype(float) if "low" in df.columns else c
        return {"close": float(c.iloc[-1]), "prev_close": float(c.iloc[-2]),
                "low_10d": float(lo.tail(10).min()), "low_20d": float(lo.tail(20).min()),
                "sma20": float(c.tail(20).mean())}

    def sector_down_pct_of(sym: str) -> float | None:
        try:
            from ..sector_rs import get_sector_etf
            etf, _fallback = get_sector_etf(sym)
            t = bars_for(etf)
            return round((t["close"] / t["prev_close"] - 1) * 100, 2) if t and t.get("prev_close") else None
        except Exception:  # noqa: BLE001
            return None

    out = put_book(rows, positions=positions, kv=kv if isinstance(kv, dict) else {}, nav_usd=nav_usd,
                   bars_for=bars_for, sector_down_pct_of=sector_down_pct_of)
    return out


def _stress_for(exposure: dict, base: str, token: str | None, *, nav_usd: float | None) -> dict:
    """Scenarios from settings-kv (defaults in stress_test), floor and betas from
    `risk_guardrails`, FX from the income settings, the next FOMC/CPI from the
    market calendar, implied moves from the ATM straddle on QQQ and SPY for
    the expiry just after the next FOMC (chain route), hike odds from the
    owner's kv input if any."""
    from ..events import load_market_calendar
    from ..income_spreads import _kv_get
    from ..stress_test import implied_move_from_straddle, stress

    def _kv(key):
        try:
            v = _kv_get(base, token or "", key) or {}
            v = v.get("value", v) if isinstance(v, dict) else v
            return json.loads(v) if isinstance(v, str) else (v or {})
        except Exception:  # noqa: BLE001
            return {}
    rg, ie, macro = _kv("risk_guardrails"), _kv("income_engine"), _kv("macro")
    gbp = float(ie.get("fx_gbp_per_usd_fallback") or 0) or None
    today = date.today()
    events = []
    try:
        for e in load_market_calendar():
            if e.kind in ("fomc", "cpi", "jobs") and e.on >= today:
                events.append({"on": e.on.isoformat(), "kind": e.kind, "title": e.title, "days": (e.on - today).days})
    except Exception as exc:  # noqa: BLE001
        events.append({"on": None, "kind": "gap", "title": f"market calendar unreadable: {str(exc)[:80]}"})
    events.sort(key=lambda x: x.get("on") or "9999")
    nxt = next((e for e in events if e.get("kind") == "fomc"), None)
    implied: dict = {}
    if nxt and nxt.get("on"):
        H = {"Authorization": f"Bearer {token}"} if token else {}
        dte = max(2, int(nxt["days"]) + 2)
        for idx in ("QQQ", "SPY"):
            try:
                r = requests.get(f"{base.rstrip('/')}/api/ibkr/chain/{idx}", params={"targetDte": dte, "maxStrikes": 6},
                                 headers=H, timeout=(5, 90))
                d = r.json() if r.status_code == 200 else {}
                spot = d.get("spot"); legs = d.get("legs") or []
                if spot and legs:
                    atm = min({float(l["strike"]) for l in legs}, key=lambda k: abs(k - float(spot)))
                    def mid(right):
                        for l in legs:
                            if float(l["strike"]) == atm and str(l.get("right", "")).upper().startswith(right) and l.get("bid") is not None and l.get("ask") is not None:
                                return (float(l["bid"]) + float(l["ask"])) / 2
                        return None
                    exp = next((str(l.get("maturityDate")) for l in legs if l.get("maturityDate")), None)
                    implied[idx] = {"expiry": exp, "atm_strike": atm, "move_pct": implied_move_from_straddle(mid("C"), mid("P"), float(spot)),
                                    "note": None if mid("C") and mid("P") else "ATM straddle not quoted (closed market or cold stream)"}
                else:
                    implied[idx] = {"move_pct": None, "note": d.get("error") or "no chain"}
            except Exception as exc:  # noqa: BLE001
                implied[idx] = {"move_pct": None, "note": str(exc)[:80]}
    odds = macro.get("fomc_hike_odds_pct")
    hike = ({"available": True, "hike_odds_pct": odds, "as_of": macro.get("fomc_hike_odds_as_of"), "source": "settings-kv macro (owner input)"}
            if odds is not None else None)
    return stress(exposure, nav_usd=nav_usd, floor_gbp=rg.get("account_floor_gbp"), gbp_per_usd=gbp,
                  scenarios=rg.get("stress_scenarios") or None, betas=rg.get("segment_beta") or {},
                  segment_to_etf=rg.get("segment_hedge_etf") or {},
                  cushion_min_gbp=rg.get("stress_cushion_min_gbp"), events=events, implied_moves=implied, hike_odds=hike, today=today)


def _env_float(name: str) -> float | None:
    v = (os.environ.get(name) or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="publish the artifact")
    ap.add_argument("--email", action="store_true",
                    help="mail the alert. Subject and body both state that this "
                         "is the LIVE account, because a paper alert read as "
                         "live (or the reverse) is the expensive mistake")
    ap.add_argument("--nav-usd", type=float,
                    default=_env_float("TRADEPRO_LIVE_NAV_USD"),
                    help="account value, so assignment obligation can be checked "
                         "against what you could actually fund. Default: the "
                         "TRADEPRO_LIVE_NAV_USD environment variable (the Mac plist "
                         "and the Lambda both set it there — never in the repo)")
    ap.add_argument("--via-api", action="store_true",
                    help="read the live book through the TradePro API's Flex route — "
                         "the ONE central IBKR connection — and never directly. The "
                         "Lambda job passes this: it holds no Flex credential and "
                         "needs none (owner, 21 Nov 2026: 'the ibkr connection is "
                         "handled centrally ... the same connection shd be used')")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    base, token = load_credentials()
    if not base:
        print("no API base configured", file=sys.stderr)
        return 2
    H = {"Authorization": f"Bearer {token}"} if token else {}

    # DIRECT when this machine holds a token, because IBKR 403s the cloud.
    # The API path stays as the fallback so nothing breaks if that changes.
    flex_token, flex_query, flex_src = ("", "", "") if a.via_api else flex_credentials()
    if flex_src:
        log.info("Flex credentials from %s", flex_src)   # the SOURCE, never the value
    positions: list[dict] = []
    if flex_token and flex_query:
        try:
            positions = _flex_direct(flex_token, flex_query)
            log.info("Flex read directly from IBKR — %d position(s)", len(positions))
        except Exception as exc:  # noqa: BLE001
            # NEVER THE TOKEN. A requests error carries the full URL, and the
            # Flex URL carries t=<token>; on 11 Nov 2026 a DNS blip printed the
            # live account's Flex token into the lane log twice. Lanes log the
            # SOURCE of a credential, never its value — this applies to errors.
            print(f"direct Flex read failed: {redact_flex(str(exc), flex_token)}", file=sys.stderr)
            return 1
    else:
        r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/flex/positions",
                         headers=H, timeout=(5, 180))
        d = r.json() if r.status_code == 200 else {}
        if not d.get("enabled"):
            print("Flex is not enabled — cannot read the live book", file=sys.stderr)
            return 2
        if d.get("error"):
            # A stale read is worse than none: this decides whether to close a
            # position before assignment.
            print(f"Flex error via the API: {d['error']}\n"
                  f"A bare 'Error 403 - Access Denied' means the request "
                  f"reached IBKR with no User-Agent — check the API's "
                  f"Flex client (IBKRFlexClient.Prepare), not the network.",
                  file=sys.stderr)
            return 1
        positions = d.get("positions") or []
    if not positions:
        print("Flex returned no positions — refusing to publish an empty book, "
              "which would read as 'you hold nothing'", file=sys.stderr)
        return 1

    from ..events import window_for_contract

    def _events_for(sym: str, expiry: str) -> dict:
        return window_for_contract(sym, expiry, api_base=base).to_dict()

    art = build(positions, nav_usd=a.nav_usd, events_for=_events_for)
    # EXPOSURE BY SEGMENT, OPTION-ADJUSTED (options review item 1, 28 Nov 2026).
    # Computed here, not in build(), so build() stays pure; a failure here is
    # reported inside the artifact, never allowed to sink the close verdicts.
    try:
        art["artifact"]["exposure"] = _exposure_for(positions, base, token, nav_usd=a.nav_usd)
    except Exception as exc:  # noqa: BLE001
        art["artifact"]["exposure"] = {"error": f"exposure not computed: {str(exc)[:160]}"}
    # PUT-BOOK MANAGER (options review item 17, 28 Nov 2026): the owner's
    # hold / accept / repair / close policy on the rows above, the cash
    # calendar by expiry month and the hedge coverage gap. Same contract as
    # the exposure block: computed beside build(), failures reported inside.
    try:
        art["artifact"]["put_book"] = _put_book_for(art["artifact"]["positions"], positions, base, token,
                                                    nav_usd=a.nav_usd)
    except Exception as exc:  # noqa: BLE001
        art["artifact"]["put_book"] = {"error": f"put book not computed: {str(exc)[:160]}"}
    # MACRO-EVENT STRESS TEST (options review item 14): the exposure block run
    # through base / hawkish shock / dovish relief against the owner's floor,
    # with the next FOMC/CPI dates and the index implied moves to just after them.
    try:
        art["artifact"]["stress"] = _stress_for(art["artifact"].get("exposure") or {}, base, token, nav_usd=a.nav_usd)
    except Exception as exc:  # noqa: BLE001
        art["artifact"]["stress"] = {"error": f"stress test not computed: {str(exc)[:160]}"}
    c = art["artifact"]["counts"]
    print(f"{c['short_puts']} short puts · {c['close']} CLOSE · {c['watch']} watch "
          f"· {c['hold']} hold · {c['unknown']} unknown")
    print(f"assignment obligation ${art['artifact']['assignment_obligation_usd']:,.0f} · "
          f"unrealised ${art['artifact']['unrealised_usd']:,.0f}\n")

    if a.email:
        rc = _mail(art["artifact"])
        if rc:
            return rc
    for x in art["artifact"].get("exposures") or []:
        print(f"  EXPOSURE  {x['detail'][:104]}")
    pb = art["artifact"].get("put_book") or {}
    if pb.get("error"):
        print(f"  PUT-BOOK  {pb['error']}")
    elif pb:
        print(f"  PUT-BOOK  {pb.get('counts')} · cheap exits now: {', '.join(pb.get('cheap_exits_now') or []) or 'none'}"
              + (f" · {pb['hedge_coverage'].get('note')}" if pb.get("hedge_coverage") else ""))
        for m in (pb.get("cash_calendar") or {}).get("months") or []:
            if m.get("status") == "SHORT":
                print(f"            cash {m['month']}: assignment ${m['assignment_usd']:,.0f} vs free ${m['free_cash_usd']:,.0f} — SHORT by ${m['short_by_usd']:,.0f}; close ahead: {', '.join(m.get('propose_closing_ahead') or [])}")
    st = art["artifact"].get("stress") or {}
    if st.get("error"):
        print(f"  STRESS    {st['error']}")
    elif st:
        for k, r in (st.get("scenarios") or {}).items():
            print(f"  STRESS    {k:14s} P&L ${r['pnl_usd']:>10,.0f}"
                  + (f"  NAV after ${r['nav_after_usd']:,.0f}" if r.get("nav_after_usd") is not None else "")
                  + (f"  cushion £{r['cushion_to_floor_gbp']:,.0f}" if r.get("cushion_to_floor_gbp") is not None else "")
                  + ("  BREACHES FLOOR" if r.get("breaches_floor") else "  thin" if r.get("thin_cushion") else ""))
        for f in st.get("flags") or []:
            print(f"  FLAG      {f}")
        for e in (st.get("events") or [])[:3]:
            print(f"            event {e.get('on')} {e.get('title')}")
        for k, v in (st.get("implied_moves") or {}).items():
            print(f"            implied move {k}: {v.get('move_pct')}% to {v.get('expiry')}" if isinstance(v, dict) else f"            implied move {k}: {v}")
    ex = art["artifact"].get("exposure") or {}
    if ex.get("error"):
        print(f"  DELTA-$   {ex['error']}")
    elif ex:
        print(f"  DELTA-$   net ${ex['net_usd']:,.0f}"
              + (f" ({ex['net_pct_of_nav']:+.0f}% of NAV)" if ex.get("net_pct_of_nav") is not None else "")
              + f" · gross long ${ex['gross_long_usd']:,.0f} · hedges ${ex['hedges_usd']:,.0f}"
              + f" · {ex['legs_priced']}/{ex['legs_total']} legs priced"
              + (f" · GAPS {ex['legs_missing']}" if ex.get("legs_missing") else ""))
        for sg in ex.get("segments") or []:
            print(f"            {sg['segment']:22s} long ${sg['long_usd']:>10,.0f}  hedge ${sg['hedge_usd']:>10,.0f}"
                  f"  net ${sg['net_usd']:>10,.0f}  {sg['share_of_gross_long_pct']:>5.1f}% of gross"
                  + ("  OVER CAP" if sg.get("over_cap") else ""))
        for f in ex.get("flags") or []:
            print(f"  FLAG      {f}")
    if art["artifact"].get("exposures"):
        print()
    for row in art["artifact"]["positions"]:
        if row["action"] in ("CLOSE", "WATCH", "UNKNOWN"):
            print(f"  {row['action']:7s} {row['symbol']:6s} {row['expiry']} "
                  f"{row['strike']:>8.1f}P {row['dte']:>4d}d  {row['why'][:88]}")

    if not a.push:
        print("\n(not pushed — pass --push)")
        return 0
    resp = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups",
                         json=art, headers=H, timeout=(5, 60))
    print(f"\npushed: HTTP {resp.status_code}")
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
