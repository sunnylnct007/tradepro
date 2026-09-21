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
# DELIBERATELY hand-maintained: this list is the ASSERTION of what should be
# running, so a job that disappears is caught rather than silently forgiven.
# Deriving it from launchctl would make removals invisible, which is the one
# thing it exists to detect. Reviewed 20 Sep, when a lane rename removed
# preearnings-watch and today-setups-push — the check flagged both as
# "not scheduled at all" the same evening, which is exactly its job.
SCHEDULED_JOBS = (
    "com.tradepro.option-chain-capture",
    "com.tradepro.swing-candidates",
    "com.tradepro.momentum-candidates",
    "com.tradepro.bar-cache-harvest-daily",
    "com.tradepro.trade-alerts",
    "com.tradepro.paper-swing-ibkr",
    "com.tradepro.screener-daily",
    "com.tradepro.mac-deploy-sync",
    # This check watches itself. A dead checker is the worst failure mode in
    # this file, because it is indistinguishable from a healthy desk unless
    # something says otherwise. Between this line and --always-mail in the
    # plist, silence at 21:45 on a weekday is unambiguous: the check did not
    # run, and that IS the alarm.
    "com.tradepro.desk-check",
)


# Worst first in the published payload, so the banner can take checks[0] as the
# headline without re-deriving severity in TypeScript.
_SEVERITY_ORDER = {BROKEN: 0, UNKNOWN: 1, WARN: 2, OK: 3}


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
    # JUDGE THE COUNT, NOT THE WORDING. This matched the verdict STRING,
    # requiring it to start with "USABLE". When every dataset came good and the
    # API switched wording to "ALL DATA CURRENT", a 6/6 result rendered as a
    # WARNING pointing at per-dataset lines that were all fine. Two definitions
    # of one fact, disagreeing — the shape this desk keeps tripping over.
    # usable/total is the fact; the sentence is decoration.
    if usable is not None and total:
        status = OK if usable >= total else (WARN if usable >= total - 1 else BROKEN)
    else:
        status = UNKNOWN
    out.append(Check(
        "Data", status, f"{verdict} — {usable}/{total} datasets usable",
        "" if status == OK else "see the per-dataset lines below"))
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


def check_broker_agrees(base: str, token: str | None) -> list[Check]:
    """Does the BROKER agree with the OMS about what we hold?

    21 Sep 2026: it did not, by 732 shares, and nothing said a word. The OMS
    had the swing sleeve LONG 61 ARWR and 13 SNOW; IBKR had it SHORT 671 and
    143. Eleven exit lots had filled at the broker and the OMS recorded two.
    The strategy kept re-selling a position it no longer owned.

    Every board, scorecard and verdict downstream of that was describing the
    OMS rather than the account — including a week of "the exit path has never
    run", which was false. This check exists so that divergence can never
    again be invisible.

    The OMS is an intent log. The broker is the golden source. Where they
    disagree, the broker is right and the desk is broken.
    """
    try:
        orders = _get(base, token, "/api/oms/orders?limit=500", timeout=45)
        if isinstance(orders, dict):
            orders = orders.get("orders") or orders.get("items") or []
        raw = _get(base, token, "/api/integrations/ibkr/positions", timeout=45)
        rows = raw if isinstance(raw, list) else (raw.get("positions") or [])
    except Exception as exc:  # noqa: BLE001
        return [Check("Broker vs OMS", UNKNOWN,
                      f"could not compare ({str(exc)[:60]})",
                      "an unverified book is an unsafe one")]

    broker: dict[str, float] = {}
    for r in rows:
        sym = str(r.get("symbol") or r.get("ticker") or "").split("_")[0].upper()
        if sym:
            broker[sym] = broker.get(sym, 0.0) + float(
                r.get("position") or r.get("quantity") or 0)

    out = []
    for sid in LIVE_STRATEGIES:
        # ONLY IBKR LANES. ichimoku_equity routes to T212 and ichimoku_fx_mr to
        # IG; comparing their books against an IBKR position list reported ten
        # and seven "divergences" on the first run — a check that cries wolf on
        # two of four lanes would be ignored by the third week, which is the
        # failure this whole file exists to avoid.
        _brokers = {str(x.get("broker") or "") for x in orders
                    if x.get("strategyId") == sid}
        if not any(bk.startswith("IBKR") for bk in _brokers):
            continue
        net: dict[str, float] = {}
        for o in sorted((x for x in orders if x.get("strategyId") == sid
                         and str(x.get("state")) == "FILLED"),
                        key=lambda x: str(x.get("createdAtUtc"))):
            sym = str(o.get("symbol") or "").split("_")[0].upper()
            q = float(o.get("filledQty") or 0)
            net[sym] = net.get(sym, 0.0) + (q if str(o.get("side", "")).upper() == "BUY" else -q)
        owned = {k: v for k, v in net.items() if abs(v) > 0.5}
        if not owned:
            continue
        # Only names the OMS claims: positions belonging to OTHER strategies
        # legitimately appear at the broker and are not this lane's business.
        bad = {k: (broker.get(k, 0.0), v) for k, v in owned.items()
               if abs(broker.get(k, 0.0) - v) > 0.5}
        if bad:
            worst = max(bad.items(), key=lambda kv: abs(kv[1][0] - kv[1][1]))
            k, (bq, oq) = worst
            total = sum(abs(b - o) for b, o in bad.values())
            out.append(Check(
                f"Broker vs OMS · {sid}", BROKEN,
                f"{len(bad)} symbol(s) disagree, {total:.0f} shares total — "
                f"worst {k}: broker {bq:+.0f} vs OMS {oq:+.0f}",
                "the BROKER is right. The sleeve is acting on a position that "
                "may not exist — do not trust its board until this is flat"))
        else:
            out.append(Check(f"Broker vs OMS · {sid}", OK,
                             f"all {len(owned)} position(s) agree with IBKR"))
    if not out:
        out.append(Check("Broker vs OMS", UNKNOWN,
                         "no strategy claims a position — nothing to reconcile"))
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
               lambda: check_broker_agrees(base, token),
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
    """Say what is broken, not that everything is.

    The first version escalated ANY broken line to
    "do not trade from these boards". On 21 Sep that fired because one
    intraday data feed was stale — while all four boards were fresh and every
    execution lane and job was fine. The owner read a verdict that told him to
    stop trading and a body that said nothing was wrong with the trading.

    An instruction that overstates its cause gets ignored, and then the night
    it means it, it is ignored too. So the verdict names the part that is
    broken: the boards are what he trades FROM, and they have their own
    answer, separate from feeds and plumbing.
    """
    broken = [c for c in checks if c.status == BROKEN]
    boards_broken = [c for c in broken if c.lane.startswith("Board")]
    trading_broken = [c for c in broken
                      if c.lane.startswith(("Execution", "Broker vs OMS"))]
    if boards_broken:
        return ("BROKEN — do not trade from these boards until fixed "
                f"({len(boards_broken)} board(s) stale or wrong)")
    if trading_broken:
        return ("EXECUTION BROKEN — the boards are readable but orders are not "
                "reaching the broker as intended")
    if broken:
        what = ", ".join(sorted({c.lane.split("·")[0].strip() for c in broken}))
        return (f"BOARDS USABLE — but {len(broken)} supporting lane(s) are "
                f"broken ({what}); the screens are fine, the plumbing is not")
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
    ap.add_argument("--push", action="store_true",
                    help="publish the verdict to the desk so it shows on the "
                         "cockpit banner (what the 21:45 job does)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    from .push_to_api import load_credentials
    base, token = load_credentials()
    checks = run_checks(base or "", token)
    verdict = verdict_of(checks)
    text = render_text(checks, verdict)
    if not args.quiet:
        print(text)

    # PUBLISH BEFORE MAILING. Owner, 20 Sep 2026: "we shd be highlighting that
    # on our dashboard if we are not able to action certian things so we can fix
    # it. observability and diagnostic is key."
    #
    # This check already knew the desk was broken — the first hand-run reported
    # the swing sleeve had opened 12 positions and closed NONE, 47 exit attempts
    # and zero reaching the broker — and said so only to a terminal. A verdict
    # nobody can see is not observability.
    #
    # Pushed FIRST because mail is the flakier leg (SMTP creds, a Lambda without
    # them, a full mailbox). The screen should not go stale because the mailer
    # had a bad night.
    if args.push:
        try:
            from .push_to_api import push
            if not base or not token:
                raise RuntimeError("no API credentials on this runtime")
            push("desk-check", {
                "label": "latest",
                "uploaded_by": "tradepro-desk-check",
                "artifact": {
                    "as_of_utc": dt.datetime.now(dt.UTC).isoformat(),
                    "verdict": verdict,
                    "n_broken": sum(1 for c in checks if c.status == BROKEN),
                    "n_warn": sum(1 for c in checks if c.status == WARN),
                    "n_ok": sum(1 for c in checks if c.status == OK),
                    # The whole list, worst first, so the banner can show the
                    # headline and the drill-down needs no second call.
                    "checks": [
                        {"lane": c.lane, "status": c.status,
                         "detail": c.detail, "fix": c.fix}
                        for c in sorted(checks, key=lambda x: _SEVERITY_ORDER.get(x.status, 9))
                    ],
                },
            }, base, token)
        except Exception as exc:  # noqa: BLE001
            # Same rule as the mailer below: failing to PUBLISH a failure is
            # itself a failure worth shouting about.
            print(f"WARNING: desk check could not be published: {str(exc)[:140]}")

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
