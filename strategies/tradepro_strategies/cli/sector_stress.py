"""tradepro-sector-stress — publish which sectors (and the market) are in a sell-off.

    tradepro-sector-stress          # print the table
    tradepro-sector-stress --push   # and publish the `sector-stress` artifact

Runs on the Mac after the daily harvest (bar-cache-harvest.sh, daily lane), so
every events window — options screen, puts board, live book, MCP — can NAME a
sector sell-off on a short-put row. See sector_stress.py for why.
"""
from __future__ import annotations

import argparse
import sys

import requests

from ..sector_stress import ARTIFACT_UNIVERSE, build


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()
    art = build()
    print(f"sector stress — bars as of "
          f"{max((s['bars_as_of'] or '') for s in art['etfs'].values()) if art['etfs'] else 'n/a'}")
    for e, s in sorted(art["etfs"].items(), key=lambda kv: kv[1]["dd_pct"] or 0):
        flag = "STRESSED" if s["stressed"] else ""
        print(f"  {e:<5} {s['dd_pct']:>7.2f}% off its 20-day high  {flag}")
    if art["missing_etfs"]:
        print(f"  MISSING bars: {', '.join(art['missing_etfs'])}", file=sys.stderr)
    if not a.push:
        return 0
    from .push_to_api import load_credentials
    base, token = load_credentials()
    H = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"universe": ARTIFACT_UNIVERSE, "label": "latest",
            "uploaded_by": "tradepro-sector-stress", "artifact": art}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=body, headers=H, timeout=(5, 120))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {ARTIFACT_UNIVERSE}: stressed = {art['stressed'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
