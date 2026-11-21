"""Publish the shadow books' scoreboard (no orders). See
tradepro_strategies/shadow_books.py and SHADOW_BOOKS_V1.md.

    tradepro-shadow-books            # print the scoreboard
    tradepro-shadow-books --push     # publish today-setups/shadow-books
"""
from __future__ import annotations

import argparse
import sys

import requests

from .. import shadow_books as B

ARTIFACT_UNIVERSE = "shadow-books"


def text(a: dict) -> str:
    lines = [f"SHADOW BOOKS ({a['version']}) — no orders; last session {(a.get('daily') or {}).get('last_session')}"]
    for r in a["scoreboard"]:
        if r.get("n") is None:
            lines.append(f"  {r['book']:22s} {r['verdict']}")
        elif "return_pct" in r:
            lines.append(f"  {r['book']:22s} {r['n']} decisions  {r['return_pct']:+.2f}% (control {r['control_return_pct']:+.2f}%)"
                         f"  worst DD {r['worst_dd_pct']:+.2f}% (control {r['control_worst_dd_pct']:+.2f}%)  → {r['verdict']}")
        else:
            lines.append(f"  {r['book']:22s} n={r['n']}  win {r.get('win_pct')}%  avg {r.get('avg_pct')}%"
                         + (f"  open {r['open']} at {r.get('open_avg_pct')}%" if r.get("open") else "")
                         + f"  → {r['verdict']}")
            if r.get("caveat"):
                lines.append(f"      ! {r['caveat']}")
    d = a.get("daily") or {}
    if d.get("missing") or d.get("poisoned") or d.get("stale"):
        lines.append(f"  NOT COVERED — missing {d.get('missing')} poisoned {d.get('poisoned')} stale {d.get('stale')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    art = B.build()
    print(text(art))
    if art["errors"]:
        print(f"books NOT computed: {art['errors']}", file=sys.stderr)
    if not a.push:
        return 0
    from .push_to_api import load_credentials
    base, token = load_credentials()
    body = {"universe": ARTIFACT_UNIVERSE, "label": "latest",
            "uploaded_by": "tradepro-shadow-books", "artifact": art}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=body,
                      headers={"Authorization": f"Bearer {token}"} if token else {},
                      timeout=(5, 60))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {ARTIFACT_UNIVERSE}")
    return 1 if art["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
