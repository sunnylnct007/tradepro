"""Nightly earnings-calendar bulk harvest trigger.

POSTs /api/earnings-calendar/harvest — the server makes ONE Finnhub
/calendar/earnings call covering the whole market and upserts the rows
into the central earnings_calendar store (migration 062). This replaces
the per-symbol fan-out that rate-limited into 182/728 EARNINGS_UNKNOWN.

Runs from launchd (com.tradepro.earnings-harvest) every night; every
outcome — including "Finnhub disabled" and "0 rows" — is written to the
central run_log so a dead calendar can never be silent.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

import requests

from ..run_log import log_run


def main() -> int:
    ap = argparse.ArgumentParser(description="Trigger the earnings-calendar bulk harvest")
    ap.add_argument("--api-base", default=os.environ.get("TRADEPRO_API_URL"),
                    help="override the API base (default: credential chain)")
    ap.add_argument("--back", type=int, default=14, help="days of past reports to (re)pull")
    ap.add_argument("--ahead", type=int, default=45, help="days of upcoming reports to pull")
    args = ap.parse_args()

    started = datetime.now(timezone.utc)
    # run_log ingest needs the bearer token, so resolve the credential
    # chain either way; an explicit --api-base only overrides the URL.
    from .push_to_api import load_credentials
    cred_base, token = load_credentials()
    base = (args.api_base or cred_base).rstrip("/")
    try:
        # A READ TIMEOUT IS NOT A DROPPED CONNECTION (28 Sep 2026).
        #
        # This died nightly from 20 Sep with "Read timed out (read timeout=120)"
        # against /api/earnings-calendar/harvest, and the calendar went 62 hours
        # stale. That matters more than it looks: the earnings gate is what
        # stops the wheel screen selling premium across a print.
        #
        # The distinction from every other retry on this desk: a ConnectionError
        # means the work never started and retrying is free. A READ timeout
        # means the server IS working and we gave up on it — retrying restarts
        # the same slow job and burns the same two minutes. So this does NOT
        # retry; it WAITS LONGER.
        #
        # 120s was never sized against the work: the server makes one Finnhub
        # call per symbol across ~5,600 symbols over a 59-day window. Ten
        # minutes is not generous, it is the first value chosen by looking at
        # what the job does rather than at what felt round.
        resp = requests.post(
            f"{base}/api/earnings-calendar/harvest",
            params={"back": args.back, "ahead": args.ahead},
            timeout=(30, 600),   # (connect, read) — a dead host still fails fast
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except requests.RequestException as exc:
        log_run("earnings-harvest", "calendar", "fail",
                error=f"harvest POST failed: {exc}", started=started, base=base, token=token)
        print(f"earnings-harvest FAIL: {exc}", file=sys.stderr)
        return 1

    if not data.get("enabled"):
        log_run("earnings-harvest", "calendar", "fail",
                error="Finnhub disabled on API — store NOT updated",
                summary=str(data.get("message") or ""), started=started, base=base, token=token)
        print("earnings-harvest FAIL: Finnhub disabled on API", file=sys.stderr)
        return 1

    fetched = int(data.get("fetched") or 0)
    upserted = int(data.get("upserted") or 0)
    truncated = data.get("suspectedTruncation")
    # A whole-market window with almost no reporters is a feed defect,
    # not a fact about the market — flag loud, don't quietly succeed.
    # Likewise a suspiciously full chunk: Finnhub silently caps bulk
    # responses (the exactly-1500 incident, 11-12 Aug 2026 — cost UBER's
    # and AAPL's real reports) — truncation must never be silent again.
    status = "ok" if (upserted >= 50 and not truncated) else "warn"
    summary = (f"{data.get('from')}..{data.get('to')}: {data.get('chunks')} chunks, "
               f"fetched {fetched}, upserted {upserted}"
               + (f" | SUSPECTED TRUNCATION: {truncated}" if truncated else ""))
    log_run("earnings-harvest", "calendar", status,
            summary=summary,
            error=None if status == "ok" else (
                "suspected chunk truncation — narrow the window" if truncated
                else "suspiciously few rows for a whole-market window"),
            started=started, base=base, token=token)
    print(f"earnings-harvest {status}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
