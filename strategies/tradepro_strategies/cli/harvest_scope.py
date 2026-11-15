"""Publish the symbols the nightly daily harvest fetches — the Data screen's scope.

Owner, 15 Nov 2026: "data screen needs sorting to hide symbols we not using".
The screen listed every symbol the store has ever held (~1,000), and the ~800
the lane no longer harvests read "STALE 3 sessions behind" — true of the files,
false as a statement about anything we use.

The scope cannot come from the Universe page's harvest switches: those said 106
names were ON while the lane harvested 180 (the engine's names arrive through
the committed universe file, not the switches). So the harvest script hands
this command the exact list it just harvested, and the screen shows those.

    tradepro-harvest-scope --symbols "$SYMS" --push
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

import requests

ARTIFACT_UNIVERSE = "harvest-scope"


def build(symbols: list[str]) -> dict:
    syms = sorted({s.strip().upper() for s in symbols if s.strip()})
    return {"as_of_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "resolution": "1d", "count": len(syms), "symbols": syms,
            "source": "bar-cache-harvest-daily.sh (the list it harvested tonight)"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbols", required=True, help="comma-separated, as harvested")
    p.add_argument("--push", action="store_true")
    a = p.parse_args(argv)
    art = build(a.symbols.split(","))
    if not art["count"]:
        print("no symbols given — refusing to publish an empty scope", file=sys.stderr)
        return 1
    print(f"harvest scope: {art['count']} symbols")
    if not a.push:
        return 0
    from .push_to_api import load_credentials
    base, token = load_credentials()
    body = {"universe": ARTIFACT_UNIVERSE, "label": "latest",
            "uploaded_by": "tradepro-harvest-scope", "artifact": art}
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
