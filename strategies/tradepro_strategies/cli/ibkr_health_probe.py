"""tradepro-ibkr-health-probe — 15-minute IBKR data-feed heartbeat.

Owner directive (10 Aug 2026): "if we cannot connect to IBKR it needs to be
logged so we can analyze how many times it fails." The screen runs already
log their own aggregate gaps, but between runs the feed's state was
invisible — dark windows could only be reconstructed by forensics. This
probe writes ONE run_log row per 15 minutes:

    closed    — market shut; auth valid, feed not exercised, nothing proven
    ok        — auth valid, a live quote snapshot served (field 31 + 7283),
                AND the fill-read path answers PRIMED
    degraded  — auth valid but the snapshot is DARK (the market-data-session
                contention / warm-up signature; auth alone proves nothing)
    fail      — auth itself failing / endpoint unreachable

The run_log timeline then answers "how often, when, and correlated with
what" — portal logins, MCP connector sessions, deploys, IBKR maintenance.
Two HTTP calls per probe; negligible pacing cost.

FILL-READ CANARY (29 Aug 2026). The probe watched the QUOTE feed and nothing
else, so the two-month blindness it should most have caught went unseen:
/iserver/account/orders is a PRIMED endpoint that answers the first call with
{"orders":[],"snapshot":false} and expects to be asked again. We asked once,
read the placeholder as fact, and recorded 57 orders with six fills at price
ZERO before anyone noticed.

The fix (e1ed6e4) re-asks. This checks that it still works, read-only, every 15
minutes -- because the owner's question about that fix was "hopefully this is the
final one", and the honest answer is not a promise, it is a canary. If the reads
go blind again this says so within the quarter-hour instead of after two months
of zero-price fills.

It cannot fail on an empty account: an unprimed answer is detected by the
snapshot FLAG, not by row count, so a genuinely quiet day still reads healthy.
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
            # "THE MARKET IS SHUT" IS NOT "SOMEONE TOOK THE SESSION" (29 Aug 2026).
            #
            # This reported `degraded — snapshot DARK ... session contention` twelve
            # times on a SATURDAY. SPY has no live print at the weekend; IBKR was
            # answering correctly and there was nothing to contend for. Twelve false
            # alarms a day is how the run log stops being read, and an unread run
            # log is how the two-month fill blindness survived.
            #
            # The same confusion was fixed on the quote ENDPOINT on 24 Aug
            # (28c25e3) and never reached this probe — a fix that does not
            # propagate is barely a fix. Reuses market_hours.is_open() rather
            # than adding a fourth copy of "is the market open" to this repo.
            open_now = None
            try:
                from ..paper import market_hours
                import datetime as _dt
                open_now = market_hours.is_open("us_equity", _dt.datetime.now(_dt.UTC))
            except Exception as exc:  # noqa: BLE001
                print(f"market-hours check unavailable: {str(exc)[:110]}", file=sys.stderr)

            if open_now is False:
                # Not "ok" — nothing was proven about the feed. A separate state,
                # so the timeline can tell "we did not look" from "we looked and
                # it was fine", which matters when reconstructing an outage.
                status = "closed"
                detail = ("market CLOSED — no live print expected. Auth valid; the feed "
                          "was not exercised. This is not session contention.")
            else:
                status = "degraded"
                detail = detail or (
                    "auth VALID but snapshot DARK (SPY served no last/IV after warm-up "
                    "retry) — market-data session contention or IBKR-side outage")

    # ── FILL-READ CANARY ──────────────────────────────────────────────
    # Read-only. Asserts the blotter answers PRIMED ("snapshot":true), which is
    # the single fact that was false for two months. Never downgrades a healthy
    # feed to fail on its own -- it degrades, because a blind fill read is a
    # real outage but a different one from a dark quote.
    fills_ok = None
    if authenticated:
        try:
            r = requests.get(f"{base}/api/integrations/ibkr/diagnose-fills",
                             headers=headers, timeout=90)
            if r.status_code == 200:
                j = r.json() or {}
                body = str(j.get("ordersBody") or "")
                # An explicit false is the failure. Absence of the flag is not:
                # IBKR has been seen to omit it, and treating that as broken
                # would cry wolf every quarter-hour.
                fills_ok = '"snapshot":false' not in body.replace(" ", "")
                if not fills_ok:
                    status = "degraded"
                    detail = ("fill read UNPRIMED — /iserver/account/orders "
                              "still answering snapshot:false after the retry. "
                              "Fills will not reconcile; this is the two-month "
                              "blindness returning.")
            else:
                fills_ok = None   # endpoint unreachable != reads broken
        except Exception as exc:  # noqa: BLE001 — a canary must never crash the probe
            fills_ok = None
            print(f"fill-read canary skipped: {str(exc)[:120]}", file=sys.stderr)

    try:
        from ..run_log import log_run
        log_run("ibkr-health", "probe", status,
                error=(detail if status != "ok" else None),
                summary=f"auth={authenticated} quote={quote_ok} fills={fills_ok}")
    except Exception as exc:  # noqa: BLE001 — the probe must never crash-loop
        print(f"run_log write failed: {exc}", file=sys.stderr)

    print(f"ibkr-health: {status} — {detail}")
    # "closed" is not a failure — exiting non-zero on every weekend tick would
    # just move the false alarm from the run log to launchd.
    return 0 if status in ("ok", "closed") else 1


if __name__ == "__main__":
    sys.exit(main())
