"""tradepro-ibkr-health-probe — 15-minute IBKR data-feed heartbeat.

Owner directive (10 Aug 2026): "if we cannot connect to IBKR it needs to be
logged so we can analyze how many times it fails." The screen runs already
log their own aggregate gaps, but between runs the feed's state was
invisible — dark windows could only be reconstructed by forensics. This
probe writes ONE run_log row per 15 minutes:

    ok        — auth valid AND a live quote snapshot served (field 31 + 7283)
    degraded  — auth valid but the snapshot is DARK (the market-data-session
                contention / warm-up signature; auth alone proves nothing)
    fail      — auth itself failing / endpoint unreachable

The run_log timeline then answers "how often, when, and correlated with
what" — portal logins, MCP connector sessions, deploys, IBKR maintenance.
Two HTTP calls per probe; negligible pacing cost.
"""
from __future__ import annotations

import sys
import time


def main() -> int:
    from .push_to_api import load_credentials
    import requests

    base, token = load_credentials()
    base = (base or "").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    status = "fail"
    detail = ""
    authenticated = False
    quote_ok = False
    try:
        r = requests.get(f"{base}/api/integrations/ibkr/status", headers=headers, timeout=45)
        d = r.json() if r.content else {}
        authenticated = bool(d.get("authenticated"))
        if not authenticated:
            detail = f"auth failed: {str(d.get('error'))[:180]}"
    except Exception as exc:  # noqa: BLE001
        detail = f"status endpoint unreachable: {exc}"

    if authenticated:
        # Two attempts with the documented warm-up pause — a single cold miss
        # is normal; two misses = the feed is genuinely dark right now.
        for attempt in range(2):
            try:
                r = requests.get(
                    f"{base}/api/integrations/ibkr/quote?symbol=SPY&fields=31,7283",
                    headers=headers, timeout=45)
                snap = (r.json() or {}).get("snapshot") or {}
                # "N/A" IS NOT A QUOTE (fixed 18 Aug 2026). IBKR returns the
                # literal STRING "N/A" for a field it cannot serve, and
                # `"N/A" is not None` is True — so this probe reported
                # "ok — auth + live snapshot" for an entire trading day while
                # every symbol's last price was dark and the wheel board ran
                # 100% on carried prices. A health check that accepts the
                # provider's own word for "I don't have this" is worse than no
                # health check: it actively certifies the outage as healthy.
                def _real(v):
                    if v is None:
                        return False
                    sv = str(v).strip().upper()
                    return sv not in ("", "N/A", "NA", "-", "NONE")
                if _real(snap.get("31")) or _real(snap.get("7283")):
                    quote_ok = True
                    break
            except Exception as exc:  # noqa: BLE001
                detail = f"quote probe error: {exc}"
            if attempt == 0:
                time.sleep(2)
        if quote_ok:
            status = "ok"
            detail = "auth + live snapshot"
        else:
            status = "degraded"
            detail = detail or (
                "auth VALID but snapshot DARK (SPY served no last/IV after warm-up "
                "retry) — market-data session contention or IBKR-side outage")

    try:
        from ..run_log import log_run
        log_run("ibkr-health", "probe", status,
                error=(detail if status != "ok" else None),
                summary=f"auth={authenticated} quote={quote_ok}")
    except Exception as exc:  # noqa: BLE001 — the probe must never crash-loop
        print(f"run_log write failed: {exc}", file=sys.stderr)

    print(f"ibkr-health: {status} — {detail}")
    return 0 if status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
