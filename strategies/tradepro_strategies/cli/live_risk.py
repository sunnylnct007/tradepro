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
          today: date | None = None, nav_usd: float | None = None) -> dict:
    """Positions -> the risk picture. Pure, so it is testable without a broker."""
    today = today or date.today()
    opts, stocks = [], []
    for p in positions:
        occ = parse_occ(str(p.get("symbol") or ""))
        (opts if occ else stocks).append((occ, p))

    shorts = [(occ, p) for occ, p in opts
              if occ and occ[2] == "P" and (p.get("quantity") or 0) < 0]
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
        "rules": {"take_profit_pct": (rules or ManageRules()).take_profit_pct,
                  "near_expiry_days": (rules or ManageRules()).near_expiry_days,
                  "loss_multiple_of_credit": (rules or ManageRules()).loss_multiple_of_credit},
        "exposures": exposures(rows, [
            {"symbol": p.get("symbol"), "qty": p.get("quantity")} for _o, p in stocks],
            sectors=_sector_map(), nav_usd=nav_usd),
        "positions": rows,
        "equity": [{"symbol": p.get("symbol"), "qty": p.get("quantity"),
                    "unrealised": _f(p.get("unrealizedPnl"))} for _o, p in stocks],
        },
    }


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
MAX_SECTOR_SHARE = float(os.environ.get("TRADEPRO_LIVE_MAX_SECTOR_SHARE", "0.35"))

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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="publish the artifact")
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
    flex_token = os.environ.get("TRADEPRO_IBKR_FLEX_TOKEN", "").strip()
    flex_query = os.environ.get("TRADEPRO_IBKR_FLEX_POSITIONS_QUERY", "").strip()
    positions: list[dict] = []
    if flex_token and flex_query:
        try:
            positions = _flex_direct(flex_token, flex_query)
            log.info("Flex read directly from IBKR — %d position(s)", len(positions))
        except Exception as exc:  # noqa: BLE001
            print(f"direct Flex read failed: {exc}", file=sys.stderr)
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

    art = build(positions, nav_usd=a.nav_usd)
    c = art["artifact"]["counts"]
    print(f"{c['short_puts']} short puts · {c['close']} CLOSE · {c['watch']} watch "
          f"· {c['hold']} hold · {c['unknown']} unknown")
    print(f"assignment obligation ${art['artifact']['assignment_obligation_usd']:,.0f} · "
          f"unrealised ${art['artifact']['unrealised_usd']:,.0f}\n")
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
