"""Publish today's gainers and losers across the harvested stocks. See
tradepro_strategies/market_movers.py.

    tradepro-market-movers            # print the top of each side
    tradepro-market-movers --push     # publish today-setups/market-movers
"""
from __future__ import annotations

import argparse
import sys

import requests

from .. import market_movers as MM

ARTIFACT_UNIVERSE = "market-movers"


def _held(base: str, token: str) -> set[str]:
    """The rotation's holdings, to mark them in the list. Display only: a read
    failure just leaves them unmarked."""
    try:
        r = requests.get(f"{base.rstrip('/')}/api/today-setups/momentum_rotation/latest",
                         headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=20)
        rows = ((r.json() or {}).get("artifact") or {}).get("rows") or []
        return {x["symbol"] for x in rows if x.get("action") in ("HOLD", "SELL")}
    except Exception:  # noqa: BLE001
        return set()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args(argv)
    base = token = None
    if a.push:
        from .push_to_api import load_credentials
        base, token = load_credentials()
    art = MM.build(held=_held(base, token) if base else None)
    print(f"session {art['session']} to {art['bar_time_ny']} NY · ranked {art['ranked']}/{art['scanned']}")
    for side in ("gainers", "losers"):
        print(side, ", ".join(f"{r['symbol']} {r['chg_pct']:+.2f}%" for r in art[side][:8]))
    if art["no_bars"] or art["behind"]:
        print(f"NOT RANKED — no bars: {len(art['no_bars'])}, behind: {art['behind'][:20]}", file=sys.stderr)
    if not a.push:
        return 0
    body = {"universe": ARTIFACT_UNIVERSE, "label": "latest",
            "uploaded_by": "tradepro-market-movers", "artifact": art}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=body,
                      headers={"Authorization": f"Bearer {token}"} if token else {}, timeout=(5, 60))
    if r.status_code >= 300:
        print(f"publish FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr)
        return 1
    print(f"published {ARTIFACT_UNIVERSE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
