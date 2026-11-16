"""Publish (and email) the daily evaluation. See tradepro_strategies/daily_evaluation.py.

    tradepro-daily-evaluation                  # print it
    tradepro-daily-evaluation --push --email   # publish today-setups/daily-evaluation (archived per day) + mail
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

import requests

from .. import daily_evaluation as E

ARTIFACT_UNIVERSE = "daily-evaluation"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--email", action="store_true")
    ap.add_argument("--session", help="YYYY-MM-DD (default: the latest settled session)")
    a = ap.parse_args(argv)
    from .push_to_api import load_credentials
    base, token = load_credentials()
    try:
        ev = E.build(base, token, date.fromisoformat(a.session) if a.session else None)
    except E.NotSettled as exc:
        print(f"NOT EVALUATED: {exc}", file=sys.stderr)
        return 1
    text = E.text_summary(ev)
    print(text)
    rc = 0
    if a.push:
        body = {"universe": ARTIFACT_UNIVERSE, "label": "latest", "uploaded_by": "tradepro-daily-evaluation",
                "artifact": {**ev, "text": text}}
        r = requests.post(f"{base}/api/ingest/today-setups", json=body,
                          headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=(5, 60))
        if r.status_code >= 300:
            print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
            rc = 1
        else:
            print(f"published {ARTIFACT_UNIVERSE} (archived as {ev['session']})")
    if a.email:
        try:
            from ..email_digest import PAPER, labelled
            from .email_digest import resolve_smtp_creds, send_email
            book = (ev["strategies"].get("momentum_rotation") or {}).get("book") or {}
            subj = (f"Daily evaluation {ev['session']} — rotation "
                    + (f"{book.get('pnl_usd', 0):+,.0f} $ ({book.get('pnl_pct') or 0:+.2f}%)" if book.get("names") else "n/a")
                    + f", {ev['conditions'].get('day_type', '?')} day")
            send_email(labelled(PAPER, subject=subj, text_body=text), resolve_smtp_creds())
            print("emailed")
        except Exception as exc:  # noqa: BLE001 — the record is published; a mail failure is reported, not fatal
            print(f"email FAILED: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
