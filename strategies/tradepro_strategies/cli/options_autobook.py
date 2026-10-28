"""Book the board's eligible options on the IBKR PAPER account.

Owner: "also i want u to start booking these automatically in paper trading",
and again later: "again i want you to automatically book option in paper
trading". Scope confirmed 28 Oct 2026: every eligible row, all structures.

WHY IT MATTERS MORE THAN IT LOOKS. Every number the board publishes about a
spread is reasoned, not measured. The wheel at least got a pre-registered
backtest and failed it; spreads have had no such test — we route to them on
sound logic and nothing has ever graded them. A paper fill is the one input no
backtest can manufacture, and without booking them there is nothing to grade.

CAPITAL GATES ARE ON HERE, AND OFF ON THE SCREEN. That is not an
inconsistency, it is the owner's own ruling restated: the SCREEN answers "is
this a good trade?", which must not depend on today's balance. BOOKING answers
"does this fit the account?", which must.

Measured on the 28 Oct board before this existed: 27 eligible rows wanted
$235,300 against a ~$247,000 account — 95% of NAV — and four cash-secured puts
were $204,000 of it, COST alone $96,500, or 39% of the account in ONE
position. Booking that is not a book, it is all-in on four names. The gates
refuse the oversized ones WITH THE NUMBER on the row; nothing is silently
skipped and nothing silently fills the account.

THE FLOOR GOES ON FIRST. For a spread this orders the legs deliberately:

    1. BUY the long put   (the floor)
    2. SELL the short put (the premium)

If the protective buy fails we hold a long put — a small, defined cost. If the
order were reversed and the buy failed, we would be left NAKED SHORT, which is
precisely the exposure the spread exists to remove. One failed HTTP call must
not silently convert a defined-risk trade into an unbounded one.

DRY RUN IS THE DEFAULT. This is a mutating route against a live broker
account; `--place` is required to send anything. Never smoke-test a mutating
route.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

from .push_to_api import load_credentials

log = logging.getLogger("tradepro.options_autobook")

#: Most capital one position may hold, in USD. Mirrors the risk engine's
#: per_position_gbp (£10,000) at the screen's display rate, and is the gate
#: that refuses COST at $96,500.
PER_POSITION_USD = float(os.environ.get("TRADEPRO_AUTOBOOK_PER_POSITION_USD", "12700"))

#: Most capital the whole day's booking may commit, in USD. A ceiling on the
#: SUM, because twenty affordable positions can still be an unaffordable book.
MAX_DEPLOY_USD = float(os.environ.get("TRADEPRO_AUTOBOOK_MAX_DEPLOY_USD", "60000"))


def _expiry_iso(row: dict) -> str | None:
    """YYYY-MM-DD, which is what the leg endpoint takes."""
    for k in ("chain_expiry", "expiry"):
        v = row.get(k)
        if not v:
            continue
        s = str(v)[:10].replace("/", "-")
        if len(s) == 8 and s.isdigit():          # YYYYMMDD
            return f"{s[:4]}-{s[4:6]}-{s[6:]}"
        if len(s) == 10 and s[4] == "-":
            return s
    return None


def _plan(rows: list[dict]) -> list[dict]:
    """Decide what to book, in expectancy order, within the capital gates.

    Returns one entry per eligible row — INCLUDING the refusals, each with its
    reason and its number. A row that is dropped without a stated reason is
    indistinguishable from one that was never considered, which is the
    false-clear this desk keeps removing.
    """
    def _ev(r: dict) -> float:
        return float((r.get("economics") or {}).get("return_on_risk_pct") or 0.0)

    planned: list[dict] = []
    committed = 0.0
    for r in sorted(rows, key=_ev, reverse=True):
        e = r.get("economics") or {}
        sym = r.get("symbol")
        cap = e.get("capital_usd")
        entry = {"symbol": sym, "structure": r.get("structure"),
                 "short_strike": e.get("short_strike"), "long_strike": e.get("long_strike"),
                 "capital_usd": cap, "return_on_risk_pct": e.get("return_on_risk_pct"),
                 "expiry": _expiry_iso(r)}
        if cap is None:
            entry.update(book=False, reason="capital not priced — the chain could "
                                            "not price this structure, so what it "
                                            "would tie up is unknown")
        elif entry["expiry"] is None:
            entry.update(book=False, reason="no expiry on the row — the leg endpoint "
                                            "needs YYYY-MM-DD and guessing it is how "
                                            "the wrong contract gets traded")
        elif cap > PER_POSITION_USD:
            entry.update(book=False,
                         reason=f"${cap:,.0f} exceeds the ${PER_POSITION_USD:,.0f} "
                                f"per-position limit")
        elif committed + cap > MAX_DEPLOY_USD:
            entry.update(book=False,
                         reason=f"${cap:,.0f} would take the day's commitment to "
                                f"${committed + cap:,.0f}, past the "
                                f"${MAX_DEPLOY_USD:,.0f} ceiling")
        else:
            committed += cap
            entry.update(book=True, reason="")
        planned.append(entry)
    return planned


def _legs_for(p: dict) -> list[dict]:
    """The legs, IN THE ORDER THEY MUST BE SENT. See the module note: the
    protective BUY goes first so a half-filled spread is long a put, never
    naked short."""
    if p["structure"] == "BULL_PUT_SPREAD" and p.get("long_strike"):
        return [
            {"side": "BUY", "strike": p["long_strike"], "what": "the floor"},
            {"side": "SELL", "strike": p["short_strike"], "what": "the premium"},
        ]
    return [{"side": "SELL", "strike": p["short_strike"], "what": "cash-secured put"}]


def _send(base: str, headers: dict, sym: str, expiry: str, strike: float,
          side: str, contracts: int) -> tuple[bool, str]:
    try:
        r = requests.post(
            f"{base.rstrip('/')}/api/integrations/ibkr/option-leg",
            json={"symbol": sym, "expiry": expiry, "strike": strike, "right": "P",
                  "side": side, "contracts": contracts,
                  # An OPENING trade on both legs. The endpoint defaults to
                  # ClosingOnly=true, which refuses a BUY that is not covering
                  # a short — correct for a close, wrong for the long leg of a
                  # new spread.
                  "closingOnly": False},
            headers=headers, timeout=(5, 60))
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:120]}"
        return True, str((r.json() or {}).get("orderId") or "sent")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:110]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--place", action="store_true",
                    help="actually send orders (default is a dry run)")
    ap.add_argument("--contracts", type=int, default=1)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    base, token = load_credentials()
    if not base:
        print("no API base configured", file=sys.stderr)
        return 2
    H = {"Authorization": f"Bearer {token}"} if token else {}

    board = requests.get(f"{base.rstrip('/')}/api/options/candidates",
                         headers=H, timeout=(5, 60)).json()
    rows = [r for r in (board.get("candidates") or []) if r.get("eligible")]
    stamp = board.get("generated_at_utc")
    print(f"board {str(stamp)[:19]} — {len(rows)} eligible\n")

    plan = _plan(rows)
    book = [p for p in plan if p["book"]]
    skip = [p for p in plan if not p["book"]]
    total = sum(p["capital_usd"] or 0 for p in book)

    for p in book:
        legs = " + ".join(f"{l['side']} {l['strike']:g}" for l in _legs_for(p))
        print(f"  BOOK   {p['symbol']:6s} {p['structure']:17s} {legs:24s} "
              f"${p['capital_usd']:>7,.0f}  RoR {p['return_on_risk_pct']}%")
    for p in skip:
        print(f"  refuse {p['symbol']:6s} {p['reason'][:96]}")

    print(f"\n{len(book)} to book, ${total:,.0f} committed · {len(skip)} refused")

    if not a.place:
        print("\nDRY RUN — nothing sent. Pass --place to book these.")
        return 0

    sent = failed = 0
    results = []
    for p in book:
        ok_all, notes = True, []
        for leg in _legs_for(p):
            ok, note = _send(base, H, p["symbol"], p["expiry"], leg["strike"],
                             leg["side"], a.contracts)
            notes.append(f"{leg['side']} {leg['strike']:g} ({leg['what']}): {note}")
            if not ok:
                ok_all = False
                # STOP THIS SPREAD. If the floor did not go on, the premium leg
                # must not be sent — that is how a defined-risk trade becomes a
                # naked short on a failed HTTP call.
                break
        (results.append({**p, "sent": ok_all, "notes": notes}))
        if ok_all:
            sent += 1
            print(f"  sent   {p['symbol']:6s} {' · '.join(notes)[:130]}")
        else:
            failed += 1
            print(f"  FAILED {p['symbol']:6s} {' · '.join(notes)[:130]}")
            if len(_legs_for(p)) > 1 and len(notes) == 1:
                print(f"         floor did not go on — premium leg NOT sent, "
                      f"so nothing is naked")

    print(f"\n{sent} booked, {failed} failed of {len(book)} attempted")
    try:
        requests.post(f"{base.rstrip('/')}/api/options/autobook-log",
                      json={"as_of": datetime.now(timezone.utc).isoformat(),
                            "board": stamp, "results": results, "refused": skip},
                      headers=H, timeout=(5, 30))
    except Exception as exc:  # noqa: BLE001 — logging must not fail the run
        log.warning("autobook log not stored: %s", str(exc)[:90])
    return 1 if (failed and not sent) else 0


if __name__ == "__main__":
    raise SystemExit(main())
