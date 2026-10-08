"""tradepro-daily-digest — "how did we do today", without being asked.

Owner, 8 Oct 2026: *"i want to monitor signals and performance"*.

Everything here already existed and had to be asked for one piece at a time:
signal replay, the order ledger, the broker book, lane logs. The gap was that
nothing ever PUSHED it. This is one mail a day that answers, in order:

    1. DID THE LANES ACTUALLY RUN?   <- the thing that failed silently
    2. What did we trade?
    3. What did the signals earn (replayed, broker-independent)?
    4. What is the book worth?

SECTION 1 IS FIRST ON PURPOSE. On 7-8 Oct both lanes sat inside a single
run for ~29 hours and then stalled for another 17. Every existing signal
said fine: launchctl reported exit=0, the job was loaded, desk-check passed.
A lane that is loaded but not TICKING is invisible to everything else we
check, and it cost a full trading day before anyone noticed. A digest that
led with P&L would have shown a quiet day and hidden the outage.

Never silently "all clear": anything that cannot be established is reported
as UNKNOWN and reads as a problem, matching desk_check's house rule.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

#: A lane is STALE if it has not completed a position seed within this many
#: minutes. The lanes tick every 15; 45 allows two missed ticks before the
#: alarm, so a single slow pass is not an alert but a genuine stall is.
LANE_STALE_MINUTES = 45

LANES = {
    "swing": "/tmp/tradepro-paper-swing-ibkr.log",
    "momentum": "/tmp/tradepro-paper-momentum-ibkr.log",
}

_SEED = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*position seed:")


def _api(path: str, base: str, token: str | None = None, timeout: int = 60):
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"} if token else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def lane_health(now_local: _dt.datetime) -> list[str]:
    """Did each lane actually tick? The question nothing else asks.

    TIMESTAMPS HERE ARE LOCAL, NOT UTC. The lane logs are written by a
    launchd job in the Mac's own timezone, so this MUST compare against
    local wall-clock. The first version of this function compared a UTC
    `now` against a BST log line and produced "(-58 min ago)" — a negative
    age, which would have meant the stall detector could never fire. A
    monitor on this desk has made that exact mistake before.
    """
    out = []
    for name, log in LANES.items():
        p = Path(log)
        if not p.is_file():
            out.append(f"  {name:9s} UNKNOWN — no log at {log}")
            continue
        last = None
        seeds_today = 0
        today = now_local.strftime("%Y-%m-%d")
        try:
            # Tail only: these logs run to hundreds of thousands of lines.
            tail = subprocess.run(["tail", "-n", "40000", log],
                                  capture_output=True, text=True, timeout=60).stdout
        except Exception as exc:  # noqa: BLE001
            out.append(f"  {name:9s} UNKNOWN — could not read the log ({str(exc)[:40]})")
            continue
        for line in tail.splitlines():
            m = _SEED.match(line)
            if not m:
                continue
            last = m.group(1)
            if last.startswith(today):
                seeds_today += 1
        if last is None:
            out.append(f"  {name:9s} UNKNOWN — no position seed found in the recent log")
            continue
        age = (now_local - _dt.datetime.strptime(last, "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        flag = "STALLED" if age > LANE_STALE_MINUTES else "ok"
        out.append(f"  {name:9s} {flag:8s} {seeds_today} pass(es) today · "
                   f"last {last} ({age:.0f} min ago)")
    return out


def orders_today(base: str, token: str | None, day: str) -> list[str]:
    try:
        d = _api("/api/oms/orders?limit=300", base, token)
    except Exception as exc:  # noqa: BLE001
        return [f"  UNKNOWN — could not read the order ledger ({str(exc)[:50]})"]
    rows = d if isinstance(d, list) else (d.get("orders") or [])
    t = [r for r in rows if str(r.get("createdAtUtc") or "")[:10] == day]
    if not t:
        return ["  none"]
    out = []
    for r in sorted(t, key=lambda x: str(x.get("createdAtUtc"))):
        bid = r.get("brokerOrderId")
        # PLACED IS NOT FILLED, and an order with no broker id never left.
        mark = f"broker={bid}" if bid else "** NO BROKER ID — never reached the broker **"
        out.append(f"  {str(r.get('createdAtUtc'))[11:16]} "
                   f"{str(r.get('strategyId') or '?'):26s} {str(r.get('side')):4s} "
                   f"{str(r.get('symbol')):12s} {str(r.get('state')):9s} {mark}")
    return out


def replay_lines(base: str) -> list[str]:
    """What the published signals earned — from the archive and the golden
    bars, never the broker. The broker book has been corrupted twice; this
    number is re-derivable by anyone."""
    from .. import signal_replay as R
    out = []
    for strat in sorted(R.STRATEGY_MODULES):
        try:
            r = R.run(strat, base)
        except Exception as exc:  # noqa: BLE001
            out.append(f"  {strat:9s} UNKNOWN — replay failed ({str(exc)[:50]})")
            continue
        s = r["summary"]
        out.append(f"  {strat:9s} {s['closed']} closed ({s['closed_wins']} win) "
                   f"realised {s['realised_pnl']:+,.2f} · "
                   f"{s['open']} open, mark {s['open_pnl']:+,.2f}")
        for t in r["closed"][-3:]:
            out.append(f"            {t['symbol']:6s} {t['reason']:7s} "
                       f"{t['ret_pct']:+6.2f}%  {t['pnl']:+9.2f}")
        if s["unmeasured"]:
            out.append(f"            {s['unmeasured']} signal(s) UNMEASURED — see replay")
    return out


def book_lines(base: str, token: str | None) -> list[str]:
    try:
        d = _api("/api/integrations/ibkr/positions?fresh=true", base, token)
    except Exception as exc:  # noqa: BLE001
        return [f"  UNKNOWN — could not read the broker ({str(exc)[:50]})"]
    p = [x for x in (d.get("positions") or []) if x.get("quantity")]
    longs = [x for x in p if x["quantity"] > 0]
    shorts = [x for x in p if x["quantity"] < 0]
    un = sum(x.get("unrealisedAbs") or 0 for x in p)
    out = [f"  {len(p)} positions — {len(longs)} long, {len(shorts)} short · "
           f"unrealised {un:+,.2f}"]
    if shorts:
        # Both live sleeves are long-only; a short here is never ours.
        out.append("  ** SHORT POSITIONS PRESENT — no live sleeve can open one **")
        for x in sorted(shorts, key=lambda y: y["quantity"])[:5]:
            out.append(f"      {x['ticker']:6s} {x['quantity']:+.0f}")
    return out


def build(base: str, token: str | None, now_utc: _dt.datetime,
          now_local: _dt.datetime) -> tuple[str, str]:
    # The order ledger is stamped UTC; the lane logs are LOCAL. Each gets the
    # clock it is actually written in — mixing them is how "(-58 min ago)"
    # happened.
    day = now_utc.strftime("%Y-%m-%d")
    lanes = lane_health(now_local)
    stalled = [l for l in lanes if "STALLED" in l or "UNKNOWN" in l]

    L: list[str] = []
    L.append(f"TradePro daily digest — {day} (generated {now_utc:%H:%M}Z)")
    L.append("")
    L.append("1. DID THE LANES RUN?")
    L += lanes
    L.append("")
    L.append("2. ORDERS TODAY")
    L += orders_today(base, token, day)
    L.append("")
    L.append("3. WHAT THE SIGNALS EARNED (replayed from the archive, not the broker)")
    L += replay_lines(base)
    L.append("")
    L.append("4. THE BOOK")
    L += book_lines(base, token)
    L.append("")
    L.append("Realised = completed trades only. Open = marks, which are not results.")
    L.append("Sample sizes are small; see STRATEGY_BOOK.md before drawing conclusions.")

    head = "LANES STALLED" if stalled else "lanes ok"
    subject = f"TradePro {day} — {head}"
    return subject, "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--send", action="store_true", help="email it (default: print)")
    a = ap.parse_args()

    base = (os.environ.get("TRADEPRO_API_BASE_URL")
            or os.environ.get("TRADEPRO_API_URL") or "").rstrip("/")
    if not base:
        print("set TRADEPRO_API_BASE_URL", file=sys.stderr)
        return 2
    token = os.environ.get("TRADEPRO_API_TOKEN")

    subject, text = build(base, token,
                          _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
                          _dt.datetime.now())
    print(text)

    if a.send:
        try:
            from types import SimpleNamespace
            from .email_digest import CRED_PATH, send_email
            cfg = json.loads(CRED_PATH.read_text())
            html = ('<pre style="font-family:monospace">'
                    + text.replace("<", "&lt;") + "</pre>")
            send_email(SimpleNamespace(subject=subject, text_body=text,
                                       html_body=html, pdf_bytes=None), cfg)
            print(f"\nsent: {subject}")
        except Exception as exc:  # noqa: BLE001 — a send failure must not hide the content
            print(f"\nEMAIL FAILED (content printed above): {exc}", file=sys.stderr)
            return 1
    # Exit 1 when a lane is stalled so the scheduler records it as a problem.
    return 1 if ("STALLED" in subject) else 0


if __name__ == "__main__":
    raise SystemExit(main())
