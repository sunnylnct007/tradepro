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

    WHY NOT THROUGH THE API. IBKR's Flex Web Service answers the deployed API
    with "Error 403 - Access Denied" and answers this machine with
    Status=Success — same URL, same token, same second. It blocks the cloud
    range the API runs in. Proxying a report through a host IBKR will not talk
    to cannot be made to work from our side.

    Every other Mac lane already reads its sources directly; this one was the
    odd case, and only because the Flex client happened to live in the backend.

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
        assignment += strike * 100 * qty
        rows.append({
            "symbol": root, "contract": p.get("symbol"), "expiry": expiry,
            "strike": strike, "qty": -qty, "dte": dte,
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
        "counts": {
            "short_puts": len(rows),
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
    total = sum((r.get("strike") or 0) * 100 * abs(r.get("qty") or 0) for r in rows)

    # 1. ONE THEME. The week's damage was a sector, not a name.
    by_sector: dict[str, float] = defaultdict(float)
    for r in rows:
        ob = (r.get("strike") or 0) * 100 * abs(r.get("qty") or 0)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="publish the artifact")
    ap.add_argument("--email", action="store_true",
                    help="mail the alert. Subject and body both state that this "
                         "is the LIVE account, because a paper alert read as "
                         "live (or the reverse) is the expensive mistake")
    ap.add_argument("--nav-usd", type=float,
                    help="account value, so assignment obligation can be checked "
                         "against what you could actually fund")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    base, token = load_credentials()
    if not base:
        print("no API base configured", file=sys.stderr)
        return 2
    H = {"Authorization": f"Bearer {token}"} if token else {}

    # DIRECT when this machine holds a token, because IBKR 403s the cloud.
    # The API path stays as the fallback so nothing breaks if that changes.
    flex_token, flex_query, flex_src = flex_credentials()
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
                  f"If this is a 403, IBKR is blocking the cloud host — set "
                  f"TRADEPRO_IBKR_FLEX_TOKEN and "
                  f"TRADEPRO_IBKR_FLEX_POSITIONS_QUERY to read directly.",
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
