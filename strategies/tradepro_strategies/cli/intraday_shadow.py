"""Publish the intraday rules' forward record (no orders). See
tradepro_strategies/intraday_shadow.py.

    tradepro-intraday-shadow            # print the record
    tradepro-intraday-shadow --push     # publish today-setups/intraday-shadow
"""
from __future__ import annotations

import argparse
import sys

import requests

from .. import intraday_shadow as S

ARTIFACT_UNIVERSE = "intraday-shadow"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    art = S.build()
    for k, r in art["rules"].items():
        print(f"{k} {r['name']}: {r['record']} · latest session {len(r['latest_session'])} trade(s)")
    if art["missing"] or art["stale"]:
        print(f"NOT COVERED — missing: {art['missing']} stale: {art['stale']}", file=sys.stderr)
    if not a.push:
        return 0
    from .push_to_api import load_credentials
    base, token = load_credentials()
    body = {"universe": ARTIFACT_UNIVERSE, "label": "latest",
            "uploaded_by": "tradepro-intraday-shadow", "artifact": art}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=body,
                      headers={"Authorization": f"Bearer {token}"} if token else {},
                      timeout=(5, 60))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {ARTIFACT_UNIVERSE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
