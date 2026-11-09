"""Publish the tracked universe so the owner can toggle it from the UI.

Owner, 9 Nov 2026: "may be we can have screen that shows all symbols and we
select which ones to harvest and use" — then "need to be a UI screen".

WHAT THIS DOES. Ingests every row of tradeable.json as the universe named
"harvest" through the API's existing universe ingest, so the existing
/universes page lists all of them with INCLUDE / EXCLUDE toggles that write
universe_overrides. universe.py reads those overrides back. No new table, no
new endpoint, no new page.

THE INGEST SCHEMA IS FIXED (ticker, name, sector, industry), so two of its
fields carry what this universe needs to show: `name` holds the harvest
reason, `industry` holds the SEED state ("ON" / "OFF"). Stated here and on
the page rather than left for a reader to infer.

A richer artifact (seed, reason, sector, bars present) is also published
under today-setups "universe-harvest" for anything that wants the detail.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import logging
import os
import pathlib
import sys

import requests

from ..universe import HARVEST_UNIVERSE_NAME, load_universe
from .push_to_api import load_credentials

log = logging.getLogger("tradepro.universe_sync")
ARTIFACT_UNIVERSE = "universe-harvest"


def _bars_present(sym: str) -> tuple[bool, bool, str | None]:
    root = pathlib.Path.home() / ".tradepro" / "bar_cache" / "us_etf" / sym
    d1 = sorted(glob.glob(str(root / "1d" / "*.parquet")))
    return bool(d1), (root / "5m").is_dir(), (pathlib.Path(d1[-1]).stem if d1 else None)


def build() -> tuple[dict, dict]:
    u = load_universe()
    ingest_rows, rich = [], []
    for r in u["symbols"]:
        sym = str(r["symbol"]).upper()
        seed = bool(r.get("harvest", True))
        has1d, has5m, last1d = _bars_present(sym)
        ingest_rows.append({"ticker": sym, "name": r.get("harvest_reason"),
                            "sector": r.get("sector"), "industry": "ON" if seed else "OFF"})
        rich.append({"symbol": sym, "seed": seed, "reason": r.get("harvest_reason"),
                     "sector": r.get("sector"), "sector_source": r.get("sector_source"),
                     "has_1d": has1d, "has_5m": has5m, "last_1d_partition": last1d,
                     "dollar_volume_median": r.get("dollar_volume_median")})
    ingest = {"universes": [{"name": HARVEST_UNIVERSE_NAME, "sourceUrl": "",
                             "fetchedAtUtc": dt.datetime.now(dt.timezone.utc).isoformat(),
                             "source": "tradeable.json", "symbols": ingest_rows}]}
    art = {"universe": ARTIFACT_UNIVERSE, "label": "latest", "uploaded_by": "tradepro-universe-sync",
           "artifact": {"as_of_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "counts": {"tracked": len(rich), "seed_on": sum(1 for x in rich if x["seed"])},
                        "symbols": rich}}
    return ingest, art


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--push", action="store_true", help="ingest + publish; default is a dry print")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ingest, art = build()
    c = art["artifact"]["counts"]
    print(f"{c['tracked']} tracked · {c['seed_on']} seeded ON · universe '{HARVEST_UNIVERSE_NAME}'")
    if not a.push:
        return 0
    base, token = load_credentials()
    if not base:
        print("no API base configured", file=sys.stderr); return 2
    H = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.post(f"{base.rstrip('/')}/api/ingest/universes", json=ingest, headers=H, timeout=(5, 120))
    if r.status_code >= 300:
        print(f"ingest FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr); return 1
    r = requests.post(f"{base.rstrip('/')}/api/ingest/today-setups", json=art, headers=H, timeout=(5, 120))
    if r.status_code >= 300:
        print(f"artifact FAILED {r.status_code}: {r.text[:300]}", file=sys.stderr); return 1
    print(f"ingested + published — toggle at /universes?name={HARVEST_UNIVERSE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
