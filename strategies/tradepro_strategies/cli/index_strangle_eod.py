"""DID THE DESK ACTUALLY WORK TODAY? — the post-close self-check.

Owner, 7 Sep 2026: "re we confident of our diagnostic and observablity we have
placed". No, and this is why.

Every failure in the first week of live running was found by a human querying
by hand, days late:

    2 Sep  the job never ran                 found in CloudWatch, days later
    1 Sep  the close failed, 4 legs overnight found by checking positions
    4 Sep  placements 404'd for days          found as placed=None beside a P&L

Not one surfaced on its own. /health/details covers deploy, api, worker and
compare-cache — nothing about whether the trading jobs ran at all. The data
capture got good this week; the ALERTING never existed.

This runs after the bell and answers one question in one place: did today work?
It checks the things that actually broke, not the things that are easy to
check, and it FAILS LOUD — non-zero exit and a subject line that says so —
because a green report nobody reads is what we already had.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging

log = logging.getLogger("tradepro.index_strangle_eod")


def audit(rows: list[dict], markets: dict) -> dict:
    """What went wrong today, as a list of stated problems.

    Every check is a failure this desk has ACTUALLY had. A check nobody has
    needed is noise, and noise is how the real signal dies.
    """
    problems: list[str] = []
    today = _dt.date.today().isoformat()
    todays = [r for r in rows if str(r.get("exchange_date") or "")[:10] == today]

    # 1. THE JOB DID NOT RUN. 2 Sep 2026: no rows, nobody noticed for days.
    if not todays:
        problems.append("NO DECISIONS RECORDED TODAY — the job did not run, or "
                        "could not push. Nothing below can be trusted.")
        return {"ok": False, "problems": problems, "checked": 0}

    placeable = {m for m, c in markets.items() if c.get("paper_trade")}
    seen = {r.get("market") for r in todays}

    # 2. A MARKET VANISHED — but a CLOSED EXCHANGE is not a fault.
    #
    # Run on 7 Sep 2026 (US Labor Day) this reported five separate problems for
    # five US markets that simply had no session. Five red lines for one
    # ordinary holiday is the cry-wolf shape that makes a report unreadable —
    # the same disease as "Missed BUYs (47)".
    #
    # Markets are grouped by timezone. If EVERY market in a zone is absent, the
    # exchange was shut and that is ONE line, stated as an observation. If some
    # traded and others did not, that is a genuine per-market fault.
    zones: dict = {}
    for m in placeable:
        zones.setdefault(markets[m].get("tz", "?"), set()).add(m)
    for tz, ms in sorted(zones.items()):
        missing = sorted(ms - seen)
        if not missing:
            continue
        if len(missing) == len(ms):
            problems.append(f"no market in {tz} was evaluated today — the "
                            f"exchange was closed, or the run for that session "
                            f"did not fire ({', '.join(missing)})")
        else:
            for m in missing:
                problems.append(f"{m}: evaluated on no expiry today, while "
                                f"other {tz} markets were — expected a row")

    monthly = [r for r in todays if r.get("expiry_kind") == "monthly"]
    for r in sorted(monthly, key=lambda x: str(x.get("market"))):
        m = r.get("market")
        if m not in placeable:
            continue
        placed, err = r.get("placed"), r.get("place_error")

        # 3. NEITHER PLACED NOR REFUSED — the silent case, twice over.
        if placed is None and not err:
            if r.get("realised_pnl") is not None or r.get("close_trigger"):
                problems.append(
                    f"{m}: has a realised P&L but NO placement record — the "
                    f"trade happened and our record of opening it did not")
            else:
                problems.append(f"{m}: no placement attempt recorded at all")
        elif placed is False and err:
            # A refusal WITH a reason is working as designed, not a problem.
            pass
        elif placed:
            # 4. OPENED AND NEVER CLOSED. 1 Sep: four legs carried overnight
            # because the close request was malformed.
            if r.get("close_trigger") is None:
                problems.append(
                    f"{m}: PLACED and still shows no close trigger — if the "
                    f"position is open past the bell this is the overnight "
                    f"exposure the time exit exists to prevent")
            # 5. FILLED AT AN UNRECORDED PRICE. The one number the whole paper
            # exercise exists to collect, and it was never written for days.
            if r.get("credit_actual") is None:
                problems.append(f"{m}: placed but credit_actual is NULL — the "
                                f"fill price was not captured and cannot be "
                                f"recovered once the position closes")

    return {"ok": not problems, "problems": problems, "checked": len(monthly)}


def main() -> int:
    from .index_strangle_paper import MARKETS
    ap = argparse.ArgumentParser(prog="tradepro-index-strangle-eod")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s")

    try:
        import requests
        from .push_to_api import load_credentials
        base, tok = load_credentials()
        r = requests.get(f"{base.rstrip('/')}/api/strangle-decisions",
                         params={"days": 2}, timeout=45,
                         headers={"Authorization": f"Bearer {tok}"} if tok else {})
        rows = (r.json() or {}).get("rows") or []
    except BaseException as exc:  # noqa: BLE001,B036 — load_credentials EXITS
        # FAIL LOUD. An unreadable log is not a clean day.
        print(f"  !! could not read the decision log: {str(exc)[:200]}")
        return 1

    res = audit(rows, MARKETS)
    print(f"index strangle — end-of-day check {_dt.datetime.now(_dt.UTC):%Y-%m-%d %H:%M}Z")
    print(f"  {res['checked']} market(s) checked")
    if res["ok"]:
        print("  OK — every placeable market was evaluated, and each either "
              "placed with a fill price and a close, or refused with a reason.")
    else:
        for p in res["problems"]:
            print(f"  !! {p}")

    if args.email:
        try:
            from types import SimpleNamespace
            from .email_digest import send_email
            from .index_strangle_paper import _email_cfg
            # THE SUBJECT CARRIES THE VERDICT. A report whose subject reads the
            # same on a good day and a bad one gets filtered into a folder and
            # stops being read — which is how every failure this week went
            # unnoticed for days.
            subject = ("[STRANGLE OK] end-of-day check clean"
                       if res["ok"] else
                       f"[STRANGLE PROBLEM] {len(res['problems'])} issue(s) today")
            lines = "\n".join(f"  - {p}" for p in res["problems"])
            text = (f"{res['checked']} market(s) checked.\n\n"
                    + ("Every placeable market was evaluated, and each either "
                       "placed with a fill price and a close, or refused with a "
                       "stated reason.\n"
                       if res["ok"] else f"Problems found today:\n{lines}\n"))
            html = ("<p>Every placeable market evaluated; each placed with a fill "
                    "price and a close, or refused with a stated reason.</p>"
                    if res["ok"] else
                    "<p><b>Problems found today:</b></p><ul>"
                    + "".join(f"<li>{p}</li>" for p in res["problems"]) + "</ul>")
            send_email(SimpleNamespace(subject=subject, text_body=text,
                                       html_body=html, pdf_bytes=None), _email_cfg())
            print(f"  email sent: {subject}")
        except Exception as exc:  # noqa: BLE001 — never lose the exit code
            print(f"  (email failed: {str(exc)[:120]})")

    if args.json:
        print(json.dumps(res, indent=1))
    # Non-zero so the SCHEDULER can tell a clean day from a broken one.
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
