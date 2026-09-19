"""Is the desk actually working today? One verdict per lane, loud on failure.

    uv run tradepro-desk-check              # print + mail if anything is wrong
    uv run tradepro-desk-check --always-mail   # what the 21:45 job runs
    uv run tradepro-desk-check --quiet      # exit code only, for cron

WHY THIS EXISTS. Owner, 19 Sep 2026: *"how come we are having continuus
issues"*. The honest answer was that he WAS the monitoring — every defect this
fortnight surfaced because he asked a precise question, not because anything
reported it. A sample of what was running unnoticed on the day this was
written:

  · the option-chain capture had been hard-failing for two days with
    `unrecognized arguments: --strangle-dte`, exit status 2, logged every run
  · the wheel board was 49 hours old and its prices had drifted far enough
    that a reviewer mistook the drift for a broken delta gate
  · the swing strategy had opened 12 positions and closed NONE, ever, because
    the exit path could not run
  · 136 symbols of option chain were fetched and discarded in a single run,
    each printing a ✓

Every one of those was visible in data the desk already had. Nothing looked.

THE ONE RULE HERE: a check may only report OK when it has POSITIVE evidence.
Anything it could not establish is UNKNOWN and reads as a problem, never as
silence. That is the opposite of how the rest of this system failed — with
success markers printed over work that never happened — and it is why this
file refuses to be clever about missing data. See
[[feedback_no_false_positives]] and [[feedback_never_infer_from_an_absence]].
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger("tradepro.desk_check")

OK, WARN, BROKEN, UNKNOWN = "OK", "WARN", "BROKEN", "UNKNOWN"
# Sort order for display and for choosing the mail subject: worst first.
RANK = {BROKEN: 0, UNKNOWN: 1, WARN: 2, OK: 3}

# Artifacts the desk publishes, and how old each may be before it is a
# problem. Hours, not "sessions": a board that has not refreshed since
# Wednesday is stale on Friday whatever the calendar says.
BOARDS = {
    "wheel": ("Wheel (put selling)", 30),
    "preearnings": ("Watch / pre-earnings", 30),
    "swing": ("Swing (mean reversion)", 30),
    "momentum": ("Momentum pullback", 30),
}

# Live strategies whose execution we can verify against the OMS.
LIVE_STRATEGIES = ("mean_reversion_swing_ibkr", "ichimoku_equity",
                   "ichimoku_equity_ibkr", "ichimoku_fx_mr")

# Below these, an execution lane is losing most of its orders and must not
# read as healthy. Set from the measured split on 19 Sep: the lanes that work
# reach the broker on ~100% of orders (T212 71/71, IG 82/83) while the broken
# one managed 9 of 48 — there is no ambiguous middle to tune against.
MIN_REACH_RATE = 0.60
MIN_EXIT_RATE = 0.50

# launchd jobs that must exit 0. `launchctl list` reports the LAST exit
# status, which is how the --strangle-dte breakage sat unnoticed: status 2,
# every run, for two days.
SCHEDULED_JOBS = (
    "com.tradepro.option-chain-capture",
    "com.tradepro.preearnings-watch",
    "com.tradepro.swing-candidates",
    "com.tradepro.momentum-candidates",
    "com.tradepro.bar-cache-harvest-daily",
    "com.tradepro.signal-watch",
    "com.tradepro.paper-swing-ibkr",
    "com.tradepro.today-setups-push",
    # This check watches itself. A dead checker is the worst failure mode in
    # this file, because it is indistinguishable from a healthy desk unless
    # something says otherwise. Between this line and --always-mail in the
    # plist, silence at 21:45 on a weekday is unambiguous: the check did not
    # run, and that IS the alarm.
    "com.tradepro.desk-check",
)


@dataclass
class Check:
    """One verdict. `detail` must carry the NUMBER that justifies it.

    Owner's standing rule: a warning that does not state its number is not a
    warning, it is a mood ([[feedback_a_warning_must_state_the_number]]).
    """

    lane: str
    status: str
    detail: str
    fix: str = ""

    @property
    def bad(self) -> bool:
        return self.status in (BROKEN, UNKNOWN)


def _hours_since(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        t = dt.datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.UTC)
        return (dt.datetime.now(dt.UTC) - t).total_seconds() / 3600
    except (ValueError, TypeError):
        return None


def _get(base: str, token: str | None, path: str, timeout: int = 30):
    import requests
    r = requests.get(f"{base.rstrip('/')}{path}",
                     headers={"Authorization": f"Bearer {token}"} if token else {},
                     timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    return r.json()


# ── the checks ────────────────────────────────────────────────────────────
def check_data(base: str, token: str | None) -> list[Check]:
    """The platform's own readiness report, promoted to a verdict.

    This endpoint already said DEGRADED while five separate things were
    broken. It was computed, served, and read by nobody.
    """
    try:
        d = _get(base, token, "/api/data-readiness")
    except Exception as exc:  # noqa: BLE001
        return [Check("Data", UNKNOWN,
                      f"could not read the readiness report ({str(exc)[:60]})",
                      "the API may be down — check /health")]
    out = []
    verdict = str(d.get("verdict") or "")
    usable, total = d.get("usable"), d.get("total")
    out.append(Check(
        "Data", OK if verdict.startswith("USABLE") and "GAPS" not in verdict
        else (WARN if usable and total and usable >= total - 1 else BROKEN),
        f"{verdict} — {usable}/{total} datasets usable",
        "" if verdict.startswith("USABLE") else "see the per-dataset lines below"))
    for x in d.get("datasets") or []:
        det = str(x.get("detail") or "")
        key = str(x.get("key"))
        # The readiness detail already says "has not run for Nh" when a feed
        # has stalled. Promote exactly that to a verdict rather than
        # re-deriving it and risking a second, disagreeing definition.
        if "has not run" in det:
            out.append(Check(f"Data · {key}", BROKEN, det[:120],
                             "the job that fills this has stopped"))
    return out


def check_boards(base: str, token: str | None) -> list[Check]:
    """Every board the owner reads must be fresh, and say so in hours."""
    out = []
    for label, (human, max_h) in BOARDS.items():
        try:
            d = _get(base, token, f"/api/today-setups/{label}/latest")
        except Exception as exc:  # noqa: BLE001
            out.append(Check(f"Board · {human}", UNKNOWN,
                             f"could not be read ({str(exc)[:50]})"))
            continue
        age = _hours_since(d.get("asOfUtc"))
        if age is None:
            out.append(Check(f"Board · {human}", UNKNOWN,
                             "published with no timestamp — age unknowable"))
            continue
        art = d.get("artifact") or {}
        rows = (art.get("candidates_v2") or art.get("rows") or [])
        n_elig = sum(1 for r in rows if r.get("eligible"))
        if age > max_h:
            out.append(Check(
                f"Board · {human}", BROKEN,
                f"{age:.0f}h old (limit {max_h}h) — {len(rows)} rows, "
                f"{n_elig} marked eligible",
                "DO NOT TRADE these rows: the prices behind them have moved"))
        else:
            out.append(Check(f"Board · {human}", OK,
                             f"{age:.0f}h old — {len(rows)} rows, {n_elig} eligible"))
    return out


def check_execution(base: str, token: str | None) -> list[Check]:
    """Did orders reach the BROKER — not the OMS, the broker.

    brokerOrderId is the only honest evidence. An order can sit in the OMS
    looking placed and never have been sent; that is precisely how swing
    raised 47 sells and filled none
    ([[project_swing_execution_outage_limit_orders]]).
    """
    try:
        orders = _get(base, token, "/api/oms/orders?limit=500", timeout=45)
        if isinstance(orders, dict):
            orders = orders.get("orders") or orders.get("items") or []
    except Exception as exc:  # noqa: BLE001
        return [Check("Execution", UNKNOWN,
                      f"could not read the OMS ({str(exc)[:60]})")]

    out = []
    for sid in LIVE_STRATEGIES:
        mine = [o for o in orders if o.get("strategyId") == sid]
        if not mine:
            continue
        reached = [o for o in mine if o.get("brokerOrderId")]
        filled = [o for o in mine if str(o.get("state")) == "FILLED"]
        sells = [o for o in mine if str(o.get("side", "")).upper() == "SELL"]
        sells_filled = [o for o in sells if str(o.get("state")) == "FILLED"]
        detail = (f"{len(mine)} orders · {len(reached)} reached the broker · "
                  f"{len(filled)} filled · exits {len(sells_filled)}/{len(sells)}")
        reach_rate = len(reached) / len(mine)
        exit_rate = (len(sells_filled) / len(sells)) if sells else None
        if not reached:
            out.append(Check(f"Execution · {sid}", BROKEN,
                             detail + " — NOTHING has ever reached the broker",
                             "brokerOrderId is null on every order"))
        elif sells and not sells_filled:
            # The exact shape that hid for weeks: entries fill, exits never do,
            # so the book can only accumulate.
            out.append(Check(f"Execution · {sid}", BROKEN, detail,
                             "it can OPEN positions and has never CLOSED one"))
        elif reach_rate < MIN_REACH_RATE or (exit_rate is not None
                                             and exit_rate < MIN_EXIT_RATE):
            # A MAJORITY LOST IS NOT A PASS. The first draft of this file
            # printed OK beside "9 reached the broker · exits 3/27" — a green
            # tick over 24 exits that never left the OMS, which is the exact
            # defect this whole file exists to catch. A partial success rate
            # must read as a problem until someone explains it.
            out.append(Check(
                f"Execution · {sid}", WARN, detail,
                f"only {reach_rate:.0%} of orders reached the broker"
                + (f" and {exit_rate:.0%} of exits filled" if exit_rate is not None else "")
                + " — the rest were dropped before the broker saw them"))
        else:
            out.append(Check(f"Execution · {sid}", OK, detail))
    if not out:
        out.append(Check("Execution", UNKNOWN,
                         "no orders found for any live strategy"))
    return out


def check_jobs() -> list[Check]:
    """launchd's last exit status per job. Non-zero means it is failing NOW.

    This is the check that would have caught `unrecognized arguments:
    --strangle-dte` on the first run instead of the third day.
    """
    try:
        raw = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception as exc:  # noqa: BLE001
        return [Check("Jobs", UNKNOWN, f"could not read launchctl ({str(exc)[:50]})")]
    status: dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            status[parts[2].strip()] = parts[1].strip()
    out = []
    for job in SCHEDULED_JOBS:
        code = status.get(job)
        short = job.replace("com.tradepro.", "")
        if code is None:
            out.append(Check(f"Job · {short}", UNKNOWN,
                             "not loaded in launchd — it is not scheduled at all"))
        elif code not in ("0", "-"):
            out.append(Check(f"Job · {short}", BROKEN,
                             f"last run exited {code}",
                             f"run it by hand to see the error: {short}"))
        else:
            out.append(Check(f"Job · {short}", OK, "last run exited 0"))
    return out


def check_round_trips(base: str, token: str | None) -> list[Check]:
    """A forward test with no completed round trip cannot judge anything.

    The scorecard said exactly this in plain English for weeks while the owner
    was asking whether the strategy worked.
    """
    out = []
    for sid in ("mean_reversion_swing_ibkr",):
        try:
            d = _get(base, token, f"/api/today-setups/scorecard-{sid}/latest")
        except Exception as exc:  # noqa: BLE001
            out.append(Check(f"Results · {sid}", UNKNOWN,
                             f"no scorecard ({str(exc)[:50]})"))
            continue
        res = (d.get("artifact") or {}).get("results") or {}
        closed = res.get("closed_trades")
        still = res.get("still_open")
        if closed is None:
            out.append(Check(f"Results · {sid}", UNKNOWN,
                             "scorecard carries no closed-trade count"))
        elif closed == 0:
            out.append(Check(
                f"Results · {sid}", WARN,
                f"0 completed round trips, {still} open — nothing to judge yet",
                "the strategy cannot be evaluated until positions close"))
        else:
            out.append(Check(f"Results · {sid}", OK,
                             f"{closed} closed, {still} open · live mean "
                             f"{res.get('live_mean_pct')}%"))
    return out


# ── assembly ──────────────────────────────────────────────────────────────
def run_checks(base: str, token: str | None) -> list[Check]:
    checks: list[Check] = []
    for fn in (lambda: check_data(base, token),
               lambda: check_boards(base, token),
               lambda: check_execution(base, token),
               check_jobs,
               lambda: check_round_trips(base, token)):
        try:
            checks.extend(fn())
        except Exception as exc:  # noqa: BLE001
            # A CHECK THAT CRASHED IS NOT A PASS. Report it as unknown, which
            # reads as a problem, rather than letting the lane vanish from the
            # report entirely and look fine by absence.
            checks.append(Check("Check harness", UNKNOWN,
                                f"a check raised {type(exc).__name__}: {str(exc)[:70]}"))
    return checks


def verdict_of(checks: list[Check]) -> str:
    if any(c.status == BROKEN for c in checks):
        return "BROKEN — do not trade from these boards until fixed"
    if any(c.status == UNKNOWN for c in checks):
        return "UNVERIFIED — something could not be checked, treat as suspect"
    if any(c.status == WARN for c in checks):
        return "USABLE WITH CAVEATS — read the warnings"
    return "USABLE — every lane checked out"


def render_text(checks: list[Check], verdict: str) -> str:
    lines = [f"TradePro desk check — {dt.datetime.now(dt.UTC):%Y-%m-%d %H:%M} UTC",
             "", verdict, ""]
    for c in sorted(checks, key=lambda c: (RANK[c.status], c.lane)):
        mark = {OK: "ok  ", WARN: "warn", BROKEN: "FAIL", UNKNOWN: "????"}[c.status]
        lines.append(f"  [{mark}] {c.lane}: {c.detail}")
        if c.fix:
            lines.append(f"         → {c.fix}")
    return "\n".join(lines)


def render_html(checks: list[Check], verdict: str) -> str:
    colour = {OK: "#1f7a3d", WARN: "#a86b00", BROKEN: "#b3261e", UNKNOWN: "#6a4fb3"}
    head = ("#b3261e" if verdict.startswith("BROKEN")
            else "#6a4fb3" if verdict.startswith("UNVERIFIED")
            else "#a86b00" if verdict.startswith("USABLE WITH") else "#1f7a3d")
    parts = [f'<div style="max-width:620px;font:14px/1.5 -apple-system,sans-serif">',
             f'<p style="font-size:16px;font-weight:600;color:{head};margin:0 0 14px">'
             f'{verdict}</p>']
    for c in sorted(checks, key=lambda c: (RANK[c.status], c.lane)):
        parts.append(
            f'<div style="border-left:3px solid {colour[c.status]};'
            f'padding:6px 10px;margin:0 0 8px;background:#fafafa">'
            f'<div style="font-weight:600">{c.lane}'
            f'<span style="color:{colour[c.status]};font-weight:500"> · {c.status}'
            f'</span></div>'
            f'<div style="color:#333">{c.detail}</div>'
            + (f'<div style="color:#666;font-size:13px">→ {c.fix}</div>' if c.fix else "")
            + '</div>')
    parts.append('<p style="color:#888;font-size:12px">A lane reports OK only on '
                 'positive evidence. Anything unverifiable is shown as ???? and '
                 'must be treated as a problem, not as silence.</p></div>')
    return "".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--always-mail", action="store_true",
                    help="mail even when everything passes")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    checks = run_checks(base or "", token)
    verdict = verdict_of(checks)
    text = render_text(checks, verdict)
    if not args.quiet:
        print(text)

    bad = [c for c in checks if c.bad or c.status == WARN]
    if not args.no_mail and (bad or args.always_mail):
        try:
            from types import SimpleNamespace

            from .email_digest import resolve_smtp_creds, send_email
            cfg = resolve_smtp_creds()
            if not cfg.get("smtp_host"):
                raise RuntimeError("no SMTP credentials on this runtime")
            n_bad = sum(1 for c in checks if c.bad)
            subject = (f"[TradePro] {verdict.split('—')[0].strip()}"
                       + (f" — {n_bad} lane(s) failing" if n_bad else ""))
            send_email(SimpleNamespace(subject=subject, text_body=text,
                                       html_body=render_html(checks, verdict),
                                       pdf_bytes=None), cfg)
        except Exception as exc:  # noqa: BLE001
            # Failing to MAIL a failure is itself a failure worth shouting
            # about — a silent alerter is the thing this file exists to
            # prevent.
            print(f"WARNING: desk check could not be mailed: {str(exc)[:120]}")

    return 1 if any(c.bad for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
