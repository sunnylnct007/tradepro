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


# A refusal that means "the desk correctly declined", not "the desk failed".
# Everything NOT on this list is treated as an operational failure, which is the
# fail-loud direction: a new refusal string shows up red until someone decides
# it is benign, rather than silently joining the clean pile.
_EXPECTED_REFUSALS = (
    "not paper-tradeable",      # India — never auto-placed, by standing rule
    "parked",                   # SPY/QQQ/GOLD, deliberately off
    "market is closed",
    "session is closed",
    "provisional",              # pre-open strikes, correctly refused
    "stand aside",              # the volatility gate declining is the gate WORKING
)


def audit(rows: list[dict], markets: dict) -> dict:
    """What went wrong today, as a list of stated problems.

    Every check is a failure this desk has ACTUALLY had. A check nobody has
    needed is noise, and noise is how the real signal dies.
    """
    problems: list[str] = []
    # Operational refusals are reported SEPARATELY from plumbing problems: they
    # are not a broken desk, they are a desk that could not trade. Both belong
    # in the subject line; conflating them would lose the distinction that makes
    # the report actionable.
    operational: list[str] = []
    placed_ok = 0
    today = _dt.date.today().isoformat()
    todays = [r for r in rows if str(r.get("exchange_date") or "")[:10] == today]

    # 1. THE JOB DID NOT RUN. 2 Sep 2026: no rows, nobody noticed for days.
    if not todays:
        problems.append("NO DECISIONS RECORDED TODAY — the job did not run, or "
                        "could not push. Nothing below can be trusted.")
        # EVERY key the caller reads, on EVERY path. Adding "placed"/
        # "operational" to the happy path only would crash the 20:20Z job the
        # first time a day recorded nothing — turning a reportable outage into
        # a silent one, which is the exact failure this file exists to catch.
        return {"ok": False, "problems": problems, "operational": [],
                "placed": 0, "placeable": 0, "checked": 0}

    placeable_markets = {m for m, c in markets.items() if c.get("paper_trade")}
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
    for m in placeable_markets:
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

    # EVERY UNIT, NOT JUST THE MONTHLY ONE.
    #
    # This filtered to expiry_kind == "monthly" and so audited half the desk.
    # It was written when monthly was the only expiry that placed; weeklies
    # began placing on 14 Sep 2026 and were never added here. On 23 Sep the
    # two weekly units failed to resolve a chain and the check could not see
    # them — it reported on two units and called the day clean while four had
    # been attempted. A unit that can trade is a unit that gets audited.
    units = [r for r in todays
             if r.get("expiry_kind") in ("monthly", "weekly")]
    for r in sorted(units, key=lambda x: (str(x.get("market")),
                                          str(x.get("expiry_kind")))):
        market = r.get("market")
        if market not in placeable_markets:
            continue
        # Name the expiry in every line: "SPX weekly" and "SPX monthly" fail
        # independently and a bare "SPX" cannot say which.
        m = f"{market} {r.get('expiry_kind')}"
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
            # A REFUSAL IS EITHER AN ANSWER OR A FAILURE, AND THIS TREATED BOTH
            # AS FINE.
            #
            # Owner, 23 Sep 2026, on receiving "[STRANGLE OK] end-of-day check
            # clean" for a session where THREE of four units never placed
            # because IBKR's chain would not resolve: "this is rubbish then".
            # He is right. The check asked only "did every refusal carry a
            # reason", which a total market-data outage passes trivially — so
            # the one failure mode that actually recurs could never turn the
            # subject line red.
            #
            # EXPECTED refusals are the desk working: a parked market, a market
            # we do not auto-place, a session that was shut, the volatility gate
            # declining. Those stay silent.
            #
            # OPERATIONAL refusals are the desk FAILING to do its job: the chain
            # would not resolve, the quote had no honest mid, the account could
            # not fund it. Those are the days that need a red subject, whatever
            # reason string they carried.
            low = str(err).lower()
            if any(t in low for t in _EXPECTED_REFUSALS):
                pass
            else:
                operational.append(f"{m}: {str(err)[:90]}")
        elif placed:
            placed_ok += 1
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

    # Units that COULD have traded = the ones that did, plus the ones that
    # failed trying. Expected refusals (parked, not paper-tradeable, gate
    # declined) are not counted as missed — they were never going to trade.
    placeable_n = placed_ok + len(operational)
    return {
        # CLEAN REQUIRES BOTH: nothing broken in the plumbing AND nothing that
        # could have traded failed to. One of four placing is not a clean day.
        "ok": not problems and not operational,
        "problems": problems,
        "operational": operational,
        "placed": placed_ok,
        "placeable": placeable_n,
        "checked": len(units),
    }


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
    print(f"  {res['checked']} unit(s) checked (monthly + weekly)")
    print(f"  placed {res['placed']} of {res['placeable']} placeable unit(s)")
    if res["ok"] and res["placeable"] == 0:
        print("  OK — no unit was eligible today (parked, gated, or shut).")
    elif res["ok"]:
        print("  OK — every placeable unit placed with a fill price and a close.")
    else:
        for p in res["problems"]:
            print(f"  !! {p}")
        for o in res["operational"]:
            print(f"  !! COULD NOT TRADE — {o}")

    if args.email:
        try:
            from types import SimpleNamespace
            from .email_digest import send_email
            from .index_strangle_paper import _email_cfg
            # THE SUBJECT CARRIES THE VERDICT. A report whose subject reads the
            # same on a good day and a bad one gets filtered into a folder and
            # stops being read — which is how every failure this week went
            # unnoticed for days.
            # THE SUBJECT MUST CARRY THE DAY, NOT THE PLUMBING. It read
            # "[STRANGLE OK] end-of-day check clean" on 23 Sep, when 3 of 4
            # units never placed because the chain would not resolve. A subject
            # that says OK on a day the desk could not trade is worse than no
            # email: it is an assurance the reader will later learn to distrust.
            if res["problems"]:
                subject = f"[STRANGLE PROBLEM] {len(res['problems'])} issue(s) today"
            elif res["operational"]:
                subject = (f"[STRANGLE COULD NOT TRADE] placed "
                           f"{res['placed']} of {res['placeable']} — "
                           f"{len(res['operational'])} unit(s) blocked")
            elif res["placeable"] == 0:
                # Nothing was ELIGIBLE — every unit was parked, gated, or shut.
                # "0 of 0 placed and closed" is technically true and reads like
                # a malfunction; say what actually happened instead.
                subject = ("[STRANGLE OK] no unit was eligible today "
                           "(parked, gated, or market shut)")
            else:
                subject = (f"[STRANGLE OK] {res['placed']} of "
                           f"{res['placeable']} placed and closed")
            lines = "\n".join(f"  - {p}" for p in res["problems"])
            oplines = "\n".join(f"  - {o}" for o in res["operational"])
            head = (f"{res['checked']} unit(s) checked across "
                    f"monthly and weekly expiries. "
                    f"PLACED {res['placed']} OF {res['placeable']} "
                    f"PLACEABLE UNIT(S).\n\n")
            body = ""
            if res["problems"]:
                body += f"Problems found today:\n{lines}\n\n"
            if res["operational"]:
                body += ("Units that COULD NOT TRADE (the desk was working; the "
                         "market or the account would not let it):\n"
                         f"{oplines}\n\n")
            if not body and res["placeable"] == 0:
                body = ("No unit was eligible today — every one was parked, "
                        "gated, or its market was shut. Nothing was missed.\n")
            elif not body:
                body = ("Every placeable unit placed with a fill price and a "
                        "close.\n")
            text = head + body
            html = (f"<p><b>Placed {res['placed']} of {res['placeable']} "
                    f"placeable unit(s).</b></p>")
            if res["problems"]:
                html += ("<p><b>Problems found today:</b></p><ul>"
                         + "".join(f"<li>{p}</li>" for p in res["problems"]) + "</ul>")
            if res["operational"]:
                html += ("<p><b>Could not trade</b> — the desk was working; the "
                         "market or the account would not let it:</p><ul>"
                         + "".join(f"<li>{o}</li>" for o in res["operational"]) + "</ul>")
            if res["ok"] and res["placeable"] == 0:
                html += ("<p>No unit was eligible today — every one was "
                         "parked, gated, or its market was shut. "
                         "Nothing was missed.</p>")
            elif res["ok"]:
                html += ("<p>Every placeable unit placed with a fill price and "
                         "a close.</p>")
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
