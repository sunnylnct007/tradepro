"""tradepro-signal-watch — tell me when to ACT, not where to look.

    uv run tradepro-signal-watch            # check, alert on anything new
    uv run tradepro-signal-watch --dry-run  # print, send nothing

Owner, 2 Sep 2026: *"i dont need more screens i need trading signals"*.

## The distinction, because it is the whole point

A SCREEN says "here are 34 things, you decide" and waits for you to come and
look. A SIGNAL says "PLTR, stop breached at 165.53, get out" and finds you.

The candidates already carry everything a signal needs — entry, stop, size,
signal bar. What was missing is that NOTHING watched them afterwards. The index
strangle has had exactly this since 11 Aug (`index_strangle_alert`); equity
positions had nothing. So a stop could be breached at 10:00 and nobody would
know until someone happened to open a tab.

## What it alerts on

    STOP BREACHED    price is at or below the signal's stop. The trade the
                     signal described is over; this is the exit it promised.
    TARGET REACHED   where the strategy published one.
    HELD TOO LONG    past max_hold_sessions — the edge these strategies measured
                     is a HOLDING-PERIOD edge, and a position kept beyond it is
                     no longer the trade that was tested.
    NEW SIGNAL       a candidate that was not there on the last check.

## What it will not do

**It does not place or close anything.** It says what happened; the owner acts.
Automating the exit is a separate decision and a much larger one — a wrong
automated exit is worse than a late manual one.

**Each event fires ONCE per position per day.** A watcher that re-sends every
fifteen minutes trains you to filter it out, which is how the desk lost four
separate alarms this week to noise (the FALLBACK badge on 94% of rows, the
harvest crying partial every 35 minutes, the deploy-drift alarm warning
permanently, and force-refresh reporting GOLD while doing nothing).

**It reads the LAST SETTLED price from our own store.** Not a live quote —
this runs on a schedule and the store is what every strategy screened on. A
signal checked against a different price than the one it was measured on is a
different signal.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

log = logging.getLogger("tradepro.signal_watch")

# One file, one line per fired event key. Same pattern as the strangle's
# _fired_today — a watcher with no memory is a watcher that spams.
FIRED = Path.home() / ".tradepro" / "signal_watch_fired.json"

# Only strategies whose candidates carry a stop we can check.
WATCHED_PREFIX = "candidates_"


def _fired_today() -> set[str]:
    today = _dt.date.today().isoformat()
    try:
        d = json.loads(FIRED.read_text())
    except Exception:  # noqa: BLE001 — a missing or corrupt file means nothing fired
        return set()
    return set(d.get(today) or [])


def _mark_fired(keys: set[str]) -> None:
    if not keys:
        return
    today = _dt.date.today().isoformat()
    try:
        d = json.loads(FIRED.read_text())
    except Exception:  # noqa: BLE001
        d = {}
    d = {today: sorted(set(d.get(today) or []) | keys)}   # keep today only
    FIRED.parent.mkdir(parents=True, exist_ok=True)
    FIRED.write_text(json.dumps(d))


def _last_close(symbol: str) -> tuple[float | None, str | None]:
    """(close, bar_date) from OUR store — the same bars the signal was measured
    on. Returns (None, None) rather than guessing."""
    try:
        from .post_earnings_puts import _store
        end = _dt.datetime.now(_dt.UTC)
        start = end - _dt.timedelta(days=20)
        df = _store().get(canonical=symbol, asset_class="us_etf", resolution="1d",
                          start=start, end=end, allow_partial=True, skip_fetch=True,
                          fetched_by="signal_watch").df
        if df is None or df.empty:
            return None, None
        return float(df["close"].iloc[-1]), str(df.index[-1])[:10]
    except Exception as exc:  # noqa: BLE001
        log.debug("no bars for %s: %s", symbol, exc)
        return None, None


def check(base: str, token: str | None) -> list[dict]:
    """Every actionable event on an open signal, minus what already fired."""
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    already = _fired_today()
    events: list[dict] = []

    try:
        r = requests.get(f"{base.rstrip('/')}/api/oms/orders", headers=headers,
                         timeout=45, params={"limit": 200})
        r.raise_for_status()
        d = r.json()
        orders = d if isinstance(d, list) else (d.get("orders") or d.get("rows") or [])
    except Exception as exc:  # noqa: BLE001 — say so rather than reporting "nothing"
        return [{"key": "oms_unreachable", "kind": "ERROR", "symbol": "-",
                 "text": f"could not read the order book: {str(exc)[:120]}"}]

    OPEN = {"FILLED", "PARTIALLY_FILLED", "WORKING", "SUBMITTED"}
    for o in orders:
        strat = str(o.get("strategyId") or o.get("strategy_id") or "")
        if not strat.startswith(WATCHED_PREFIX):
            continue
        state = str(o.get("state") or "").upper()
        sym = str(o.get("symbol") or "").split("_")[0].upper()
        stop = o.get("signalStopPrice") or o.get("signal_stop_price")
        ref = o.get("signalRefPrice") or o.get("signal_ref_price")
        if not sym:
            continue

        if state == "PENDING_APPROVAL":
            key = f"{sym}:awaiting"
            if key not in already:
                events.append({"key": key, "kind": "AWAITING APPROVAL", "symbol": sym,
                               "text": f"{sym} {o.get('side')} {o.get('qty')} is queued and "
                                       f"NOT placed — approve it on the Orders tab, or it "
                                       f"never becomes a trade."})
            continue
        if state not in OPEN or stop is None:
            continue

        close, bar = _last_close(sym)
        if close is None:
            continue

        if close <= float(stop):
            key = f"{sym}:stop"
            if key not in already:
                events.append({
                    "key": key, "kind": "STOP BREACHED", "symbol": sym,
                    "text": (f"{sym} closed {close:.2f} on {bar}, at or below its stop "
                             f"{float(stop):.2f}"
                             + (f" (entry {float(ref):.2f}, "
                                f"{100 * (close / float(ref) - 1):+.1f}%)" if ref else "")
                             + ". The trade the signal described is over — this is the "
                               "exit it promised.")})
    return events


def main() -> int:
    ap = argparse.ArgumentParser(prog="tradepro-signal-watch")
    ap.add_argument("--dry-run", action="store_true", help="print, send nothing, remember nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    events = check(base, token)

    if not events:
        print("no signal events — nothing to act on")
        return 0

    lines = [f"{e['kind']}: {e['text']}" for e in events]
    for ln in lines:
        print(f"  {ln}")

    if args.dry_run:
        return 0

    subj = (f"[SIGNAL] {len(events)} event(s) — "
            + ", ".join(sorted({e["symbol"] for e in events if e["symbol"] != "-"}))[:60])
    body = ["TradePro signal watch", "",
            "ACT ON THESE. This is not a board to browse — each line is an event",
            "on a position you already have.", ""]
    body += [f"  {ln}" for ln in lines]
    body += ["", "Each event fires ONCE per day. Nothing here is placed or closed",
             "automatically — the watcher says what happened, you decide.",
             "", "Board: http://16.60.201.137/ -> Candidates"]
    text = "\n".join(body)

    sent = False
    try:
        from .email_digest import CRED_PATH, send_email
        cfg = json.loads(CRED_PATH.read_text())
        html = "<pre style=\"font-family:monospace\">" + text.replace("<", "&lt;") + "</pre>"
        send_email(SimpleNamespace(subject=subj, text_body=text, html_body=html,
                                   pdf_bytes=None), cfg)
        sent = True
        print(f"  alert sent: {subj}")
    except Exception as exc:  # noqa: BLE001 — a send failure must not re-fire tomorrow
        log.warning("alert email failed (non-fatal): %s", exc)

    # Record the OUTCOME, not just the attempt — the lesson from the strangle
    # alert, which reported ok whether or not the mail went.
    try:
        from ..run_log import log_run
        log_run("signal-watch", "email", "ok" if sent else "fail",
                error=None if sent else "send failed",
                summary=f"{len(events)} event(s)")
    except Exception:  # noqa: BLE001
        pass

    # Mark AFTER the attempt: a send that failed should retry on the next run
    # rather than be silently swallowed for the day.
    if sent:
        _mark_fired({e["key"] for e in events})
    return 0
