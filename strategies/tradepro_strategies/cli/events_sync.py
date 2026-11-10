"""tradepro-events-sync — push the seeded market calendar into `catalysts`.

The seed is `strategies/events/market_calendar.json` (FOMC decisions,
elections, …), every row stamped symbol=MARKET, source=market_calendar. The
POST is an upsert on (symbol, kind, occurs_on, source), so re-running after
an edit updates titles in place and never touches an owner-added row
(source=operator).

    tradepro-events-sync            # validate the file, print what is in it
    tradepro-events-sync --push     # and write it to the API

Then any lane asking `events_between(...)` names these. See events.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import requests

from ..events import MARKET_CALENDAR_PATH, MARKET_SYMBOL, load_market_calendar


def build(path=None) -> list[dict]:
    """The POST bodies, in the API's own casing."""
    return [{
        "Symbol": MARKET_SYMBOL, "Kind": e.kind, "OccursOn": e.on.isoformat(),
        "Title": e.title, "Source": e.source, "Severity": e.severity,
        "Status": "active", "Payload": {"seeded_by": "tradepro-events-sync"},
    } for e in load_market_calendar(path)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="write the rows to /api/catalysts")
    a = ap.parse_args()

    bodies = build()
    today = date.today()
    future = [b for b in bodies if date.fromisoformat(b["OccursOn"]) >= today]
    print(f"{MARKET_CALENDAR_PATH.name}: {len(bodies)} event(s), {len(future)} still ahead")
    for b in future:
        print(f"  {b['OccursOn']}  {b['Kind']:<10} {b['Title']}")
    if not future:
        print("WARNING: nothing in the seed is in the future — the calendar needs "
              "new dates", file=sys.stderr)
    if not a.push:
        return 0

    from .push_to_api import load_credentials
    base, token = load_credentials()
    H = {"Authorization": f"Bearer {token}"} if token else {}
    ok = 0
    for b in bodies:
        r = requests.post(f"{base}/api/catalysts/", json=b, headers=H, timeout=(5, 60))
        if r.status_code >= 300:
            print(f"FAILED {b['OccursOn']} {b['Kind']}: {r.status_code} {r.text[:200]}",
                  file=sys.stderr)
            continue
        ok += 1
    print(f"pushed {ok}/{len(bodies)} to {base}/api/catalysts/")
    return 0 if ok == len(bodies) else 1


if __name__ == "__main__":
    sys.exit(main())
