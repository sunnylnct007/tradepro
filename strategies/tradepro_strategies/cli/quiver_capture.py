"""Record Quiver's insider feed daily — because it has no history endpoint.

    uv run tradepro-quiver-capture            # capture + push today's slice
    uv run tradepro-quiver-capture --dry-run  # print, push nothing

THE REASON THIS EXISTS, measured 15 Sep 2026:

    /beta/historical/congresstrading/{t}   200 — ten years (2016 → 2026)
    /beta/historical/govcontractsall/{t}   200 — 3.5 years
    /beta/historical/insiders/{t}          404 — NOTHING

Congress and contracts can be studied whenever we choose; their past is
already on the server. Insider filings cannot. The live endpoint serves a
rolling window, so the only insider history we will ever have for OUR names
is the history we start keeping today, and a day not captured is gone.

That matters because insider CLUSTER buying is the one item on this platform
with a documented prior in the literature. If we ever want to know whether it
works on the names we actually trade — pre-registered gates, honest answer —
this file is the prerequisite. Capturing costs one request a day; not
capturing costs the question.

Deliberately dumb: it stores what the feed said, unfiltered and unjudged,
under a dated label on the existing artifact rails. No interpretation is
baked in, because we do not yet know which interpretation we will want.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging

log = logging.getLogger("tradepro.quiver_capture")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=7,
                    help="keep filings dated within N days (default 7). The "
                         "feed reaches back years; we only need the new edge, "
                         "and overlap makes the daily slices self-healing if a "
                         "run is missed.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ..quiver import _get
    from ..universe import universe_symbols
    from .push_to_api import load_credentials

    uni = {s.upper() for s in universe_symbols(strict=False)}
    rows = _get("/beta/live/insiders")
    if not rows:
        log.warning("no insider rows returned — nothing captured")
        return 0

    cutoff = (_dt.date.today() - _dt.timedelta(days=args.days)).isoformat()
    kept = []
    for x in rows:
        d = str(x.get("Date") or "")[:10]
        if d < cutoff:
            continue
        sym = str(x.get("Ticker") or "").upper()
        kept.append({
            "symbol": sym,
            "in_universe": sym in uni,
            "date": d,
            "file_date": str(x.get("fileDate") or "")[:19],
            "name": x.get("Name"),
            "code": x.get("TransactionCode"),
            "acquired_disposed": x.get("AcquiredDisposedCode"),
            "shares": x.get("Shares"),
            "price": x.get("PricePerShare"),
            "shares_after": x.get("SharesOwnedFollowing"),
            "ownership": x.get("directOrIndirectOwnership"),
            "officer_title": x.get("officerTitle"),
            "is_officer": x.get("isOfficer"),
            "is_director": x.get("isDirector"),
            "is_ten_pct": x.get("isTenPercentOwner"),
        })

    ours = [k for k in kept if k["in_universe"]]
    buys = [k for k in ours if k["code"] == "P" and k["acquired_disposed"] == "A"]
    art = {
        "as_of_utc": _dt.datetime.now(_dt.UTC).isoformat(),
        "window_days": args.days,
        "source": "quiver /beta/live/insiders",
        "why_captured": ("no historical insider endpoint exists (404); this is "
                         "the only record of the feed we will ever have"),
        "counts": {"rows": len(kept), "in_universe": len(ours),
                   "open_market_buys_in_universe": len(buys)},
        "rows": kept,
    }
    log.info("captured %d filing(s), %d in universe, %d open-market buys",
             len(kept), len(ours), len(buys))

    if args.dry_run:
        print(json.dumps({k: v for k, v in art.items() if k != "rows"}, indent=1))
        return 0

    import requests
    base, token = load_credentials()
    base = base.rstrip("/")
    label = f"quiver-insiders-{_dt.date.today().isoformat()}"
    r = requests.post(f"{base}/api/ingest/today-setups",
                      json={"universe": label, "label": "latest",
                            "uploaded_by": "quiver-capture", "artifact": art},
                      headers={"Authorization": f"Bearer {token}"}, timeout=60)
    log.info("capture push (%s) → HTTP %s", label, r.status_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
