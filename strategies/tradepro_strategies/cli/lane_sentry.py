"""Expected-output sentry — a dead lane must never read as a quiet day.

THE INCIDENT THIS EXISTS FOR (17-18 Sep 2026). An indentation slip in #149
made the strangle's entire placement pass dead code for two full sessions.
Every surface stayed green: run_log said ok, 16 decision rows persisted daily,
the email went out. The ONLY trace was `placed` being NULL instead of False —
"we never tried" instead of "we tried and could not" — and nothing was looking.

The existing `tradepro-heartbeat` proves the MACHINE is alive. This proves the
LANES produced what the schedule says they owe: it reads the OUTPUT tables
through the same API the desk uses, and fails loud when an expected artifact
is missing — or present but carrying the silent-death signature above.

Checks are ASSESSABLE only inside their own windows (a capture can't be
missing before the capture hour), and every failure states the number
([[feedback_a_warning_must_state_the_number]]). One run_log row per run:
status=ok, or status=error naming every lane that failed — which lands on the
cockpit's RunLogCard, the one surface the owner already reads.

Read-only against the API. No state, no side effects beyond the run_log row.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from zoneinfo import ZoneInfo

log = logging.getLogger("tradepro.lane_sentry")

ET = ZoneInfo("America/New_York")

# ── which session is owed output ─────────────────────────────────────────


def expected_session(now_utc: dt.datetime) -> dt.date | None:
    """The most recent US session date whose lanes are fully owed.

    Before 15:00Z on a weekday the morning lanes (strangle 14:12Z) may not
    have fired yet, so the previous weekday is the one owed. Weekends roll
    back to Friday. Returns None only if the calendar itself is broken.

    HOLIDAYS ARE NOT MODELLED — on a US holiday the strangle rows exist with
    session_state != 'open' and every check that requires an open session
    SKIPS, so a holiday reads as 'nothing assessable', never as a failure.
    """
    d = now_utc.date()
    if now_utc.hour < 15:  # morning lanes not yet owed for today
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


# ── the checks. Each returns (status, detail) with the NUMBER in detail. ──
# status: "ok" | "fail" | "skip" (skip = not assessable right now, said why)


def check_strangle_decisions(rows: list[dict], session: dt.date) -> tuple[str, str]:
    """The decision lane ran: rows exist for the session."""
    n = sum(1 for r in rows if str(r.get("exchange_date", ""))[:10] == str(session))
    if n == 0:
        return "fail", f"0 decision rows for {session} — the 14:12Z Lambda never landed"
    return "ok", f"{n} decision rows for {session}"


def check_strangle_placement_attempted(rows: list[dict], session: dt.date,
                                       us_markets: set[str]) -> tuple[str, str]:
    """THE NULL DETECTOR. On an open US session every US row must carry a
    placement VERDICT: placed=True, or placed=False with the refusal recorded.
    placed=NULL means the placement pass never ran — the 17-18 Sep signature.

    A day of recorded refusals (16 Sep: 16/16 False, every one with a reason)
    PASSES this check. It is not "did we fill", it is "did the code that
    decides even execute".
    """
    day = [r for r in rows if str(r.get("exchange_date", ""))[:10] == str(session)
           and r.get("market") in us_markets]
    if not day:
        return "skip", f"no US rows for {session} yet"
    open_rows = [r for r in day if r.get("session_state") == "open"]
    if not open_rows:
        return "skip", f"{len(day)} US rows but none decided in an open session (holiday?)"
    null_rows = [r for r in open_rows if r.get("placed") is None]
    if null_rows:
        mkts = sorted({r["market"] for r in null_rows})
        return "fail", (f"{len(null_rows)} of {len(open_rows)} US rows have placed=NULL "
                        f"({', '.join(mkts)}) — placement never ATTEMPTED, the dead-code "
                        "signature from 17-18 Sep")
    placed = sum(1 for r in open_rows if r.get("placed"))
    return "ok", (f"{len(open_rows)} US rows all carry a verdict "
                  f"({placed} placed, {len(open_rows) - placed} refused-with-reason)")


def check_chain_capture(legs: list[dict], session: dt.date, root: str) -> tuple[str, str]:
    """The nightly chain lane captured this root for the session, at BOTH
    strangle DTEs (two distinct expiries — the per-expiry IV work needs the
    pair, not one)."""
    day = [x for x in legs if str(x.get("capture_date", ""))[:10] == str(session)]
    if not day:
        return "fail", f"{root}: 0 legs captured for {session}"
    exps = {str(x.get("expiry", ""))[:10] for x in day}
    if len(exps) < 2:
        return "fail", (f"{root}: {len(day)} legs for {session} but only "
                        f"{len(exps)} expiry({', '.join(sorted(exps))}) — the "
                        "two-DTE capture did not run")
    return "ok", f"{root}: {len(day)} legs across {len(exps)} expiries"


# ── wiring ────────────────────────────────────────────────────────────────


def run(now_utc: dt.datetime | None = None) -> tuple[int, list[str]]:
    import requests
    from .push_to_api import load_credentials
    from .index_strangle_paper import MARKETS

    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    session = expected_session(now_utc)
    base, tok = load_credentials()
    hdr = {"Authorization": f"Bearer {tok}"} if tok else {}

    us_markets = {m for m, c in MARKETS.items() if c.get("ccy") == "$"}
    # The chain roots the strangle actually trades — SPX and XSP are the desk;
    # derived from MARKETS (broker_symbol present + not parked), never retyped.
    live_roots = [c["chain_symbol"] for m, c in MARKETS.items()
                  if c.get("broker_symbol") and not c.get("placement_parked")
                  and c.get("chain_symbol")]

    results: list[tuple[str, str, str]] = []  # (lane, status, detail)

    try:
        r = requests.get(f"{base}/api/strangle-decisions", params={"days": 4},
                         headers=hdr, timeout=30)
        rows = (r.json() or {}).get("rows", []) if r.status_code == 200 else None
    except Exception as exc:  # noqa: BLE001
        rows = None
        results.append(("api", "fail", f"decision log unreadable: {str(exc)[:80]}"))
    if rows is not None:
        results.append(("strangle-decisions", *check_strangle_decisions(rows, session)))
        results.append(("strangle-placement",
                        *check_strangle_placement_attempted(rows, session, us_markets)))

    # Chain capture is owed only after the nightly lane's window (ends ~23:30
    # London). Assess it when checking a PREVIOUS session, or late in the day.
    capture_owed = session < now_utc.date() or now_utc.hour >= 23
    for root in sorted(set(live_roots)):
        if not capture_owed:
            results.append((f"chain-capture {root}", "skip",
                            f"capture for {session} not owed until 23:00Z"))
            continue
        try:
            r = requests.get(f"{base}/api/options/quotes-daily/{root}",
                             params={"days": 3}, headers=hdr, timeout=30)
            body = r.json() if r.status_code == 200 else []
            legs = body if isinstance(body, list) else (body.get("quotes") or [])
        except Exception as exc:  # noqa: BLE001
            results.append((f"chain-capture {root}", "fail",
                            f"unreadable: {str(exc)[:80]}"))
            continue
        results.append((f"chain-capture {root}", *check_chain_capture(legs, session, root)))

    failures = [f"{lane}: {detail}" for lane, st, detail in results if st == "fail"]
    for lane, st, detail in results:
        mark = {"ok": "✓", "fail": "✗", "skip": "·"}[st]
        print(f"  {mark} {lane:28s} {detail}", flush=True)

    # ONE row, loud on the cockpit. Never raises (run_log swallows delivery).
    from ..run_log import log_runs
    log_runs([{
        "process": "lane-sentry", "kind": "watch",
        "status": "error" if failures else "ok",
        "error": ("; ".join(failures))[:900] if failures else None,
        "summary": (f"session {session}: "
                    f"{sum(1 for _, s, _ in results if s == 'ok')} ok, "
                    f"{len(failures)} failed, "
                    f"{sum(1 for _, s, _ in results if s == 'skip')} skipped"),
    }], base=base, token=tok)
    return (1 if failures else 0), failures


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    logging.basicConfig(level=logging.WARNING)
    code, failures = run()
    if failures:
        print(f"\nLANE SENTRY: {len(failures)} lane(s) FAILED", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
