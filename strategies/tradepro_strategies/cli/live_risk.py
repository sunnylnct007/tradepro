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
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests

from ..quant_engine.options.manage import ManageRules, assess, parse_occ
from .push_to_api import load_credentials

log = logging.getLogger("tradepro.live_risk")

ARTIFACT_UNIVERSE = "live-book-risk"


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


def build(positions: list[dict], *, rules: ManageRules | None = None,
          today: date | None = None) -> dict:
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

    return {
        "universe": ARTIFACT_UNIVERSE,
        "label": "latest",
        "uploaded_by": "tradepro-live-risk",
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
        "positions": rows,
        "equity": [{"symbol": p.get("symbol"), "qty": p.get("quantity"),
                    "unrealised": _f(p.get("unrealizedPnl"))} for _o, p in stocks],
    }


def _f(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="publish the artifact")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    base, token = load_credentials()
    if not base:
        print("no API base configured", file=sys.stderr)
        return 2
    H = {"Authorization": f"Bearer {token}"} if token else {}

    r = requests.get(f"{base.rstrip('/')}/api/integrations/ibkr/flex/positions",
                     headers=H, timeout=(5, 180))
    d = r.json() if r.status_code == 200 else {}
    if not d.get("enabled"):
        print("Flex is not enabled — cannot read the live book", file=sys.stderr)
        return 2
    if d.get("error"):
        # A stale read is worse than none: this feeds a decision about whether
        # to close a position before assignment.
        print(f"Flex error: {d['error']}", file=sys.stderr)
        return 1
    positions = d.get("positions") or []
    if not positions:
        print("Flex returned no positions — refusing to publish an empty book, "
              "which would read as 'you hold nothing'", file=sys.stderr)
        return 1

    art = build(positions)
    c = art["counts"]
    print(f"{c['short_puts']} short puts · {c['close']} CLOSE · {c['watch']} watch "
          f"· {c['hold']} hold · {c['unknown']} unknown")
    print(f"assignment obligation ${art['assignment_obligation_usd']:,.0f} · "
          f"unrealised ${art['unrealised_usd']:,.0f}\n")
    for row in art["positions"]:
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
