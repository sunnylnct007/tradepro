"""AWS Lambda entry point for TradePro's light scheduled jobs.

WHY LAMBDA AND NOT THE MAC.

The MacBook was chosen for one reason — it runs the local LLM (Ollama), which
genuinely needs the hardware. Everything else scheduled on it is there by
accident of history, and it inherits the Mac's weaknesses: it must be awake
(hence `com.tradepro.stay-awake`), it sleeps, it travels, and 33 launchd jobs
now depend on a laptop being open.

Owner, 29 Aug 2026: "macbook was choosen for runnign llm", "all these light
scedhued jobs can mive to lambda", "they shd start now going to aws lambda so
not dependent on macbook".

WHY NOT THE EXISTING EC2 BOX. It auto-stops overnight (see
`project_aws_overnight_stop`), and the Indian-market job runs at 04:00 BST when
that box is asleep. Lambda has no such window.

WHICH JOBS CAN MOVE. Only those with no local dependency. Verified by import:

    index_strangle_paper   yfinance, yahoo_session, email_digest   -> YES
    index_strangle_alert   yfinance, yahoo_session, email_digest   -> YES
    post_earnings_puts     BarStore (S3 read-through)              -> YES, needs
                                                                       the bucket
    swing/momentum         BarStore + heavy pandas                 -> later
    ollama / LLM           local model                             -> NEVER

TWO WAYS IN, deliberately:
  * EventBridge, on a schedule — the daily run
  * A direct invoke carrying {"job": "..."} — the UI trigger, because the owner
    asked for it in the same breath: "just remember i need UI function t
    trigger them as I need it"

Both paths run the SAME function. A UI-triggered run and a scheduled run must
never be able to diverge, which is what happens when someone adds a second
entry point "just for the button".
"""
from __future__ import annotations

import json
import logging
import os
import traceback

log = logging.getLogger()
log.setLevel(logging.INFO)

# An image older than this means aws-lambda-jobs has stopped deploying, since it
# runs on every push touching strategies/. Generous on purpose: a quiet fortnight
# in that directory is plausible, a quiet month is not.
STALE_AFTER_DAYS = 21

# job name -> (module, argv). Nothing here may need local disk beyond /tmp.
JOBS: dict[str, tuple[str, list[str]]] = {
    # --place --place-shadow: place EVERY day, including days the volatility
    # gate refused, tagged shadow=true.
    #
    # Owner, 31 Aug 2026: "the whole purpose is to see the effect of the index
    # strangle in paper trading ... i know we have rule saying enter when
    # volatality is less but lets capture execution ... as that will be key to
    # our platform".
    #
    # He is right and it costs nothing. Every published figure for this
    # strategy is Black-Scholes off a volatility index — no skew, no bid-ask,
    # no evidence anyone would be filled there. Real paper fills are the one
    # input no backtest can manufacture, and the days the gate REFUSED are the
    # ones that measure what the gate is actually worth. On paper that
    # measurement is free; skipping it throws away the only honest prices this
    # project will ever have.
    #
    # The gate is NOT weakened — it still decides `status`, and every fill is
    # tagged signal vs shadow so the two populations are never averaged
    # together.
    "index_strangle_paper": ("tradepro_strategies.cli.index_strangle_paper",
                             ["--email", "--place", "--place-shadow", "--quote"]),
    # The exit half: profit target, or the bell. Runs through the session.
    "index_strangle_close": ("tradepro_strategies.cli.index_strangle_close", []),
    "index_strangle_alert": ("tradepro_strategies.cli.index_strangle_alert", ["--email"]),
    "post_earnings_puts":   ("tradepro_strategies.cli.post_earnings_puts", ["--push"]),
    # Pre-earnings per-symbol spec engine (Phase 1, alerts only). State,
    # dedupe keys and config all live in settings-kv, so the Mac schedule and
    # this one can overlap without double-alerting — the fired-key store is
    # shared. Daily bars come from the BarStore via S3 read-through (same as
    # post_earnings_puts); intraday 15m from yfinance, labelled.
    "preearnings_watch":    ("tradepro_strategies.cli.preearnings_watch", []),

    # ── Paper sleeves, moving off the MacBook ───────────────────────────
    #
    # Owner, 6 Sep 2026: "we are not leveraging olama so i would prefer all to
    # lambda", and "ensure we do not create more regression".
    #
    # The Mac's one justification was Ollama, which has produced FIVE verdicts,
    # newest 1 August — 35 days stale — while holding 8GB resident. Meanwhile
    # 30 launchd agents depend on a laptop staying awake, two of which PLACE
    # ORDERS.
    #
    # These run here WITHOUT ib_insync: it is imported lazily and the package
    # works without it. Bars come from the shared cache, account state from the
    # IBKR Web API, and orders route through the OMS to the backend — all
    # network calls Lambda makes as well as the Mac does.
    #
    # REGISTERED IN 'manual' PLACEMENT MODE AND WITHOUT --push, DELIBERATELY.
    # The Mac agents are STILL RUNNING. If both fired in auto mode the same
    # signal would be placed TWICE. These exist to be invoked by hand and
    # compared against the Mac's output; they get no EventBridge rule and no
    # auto mode until that comparison passes and the Mac agent is unloaded.
    # Doubling a live order to save a migration step is not a trade anyone
    # chose.
    "paper_swing_dryrun": (
        "tradepro_strategies.cli.paper_session",
        ["--broker", "ibkr", "--strategy", "mean_reversion_swing",
         "--strategy-id", "mean_reversion_swing_ibkr", "--universe", "tradeable",
         "--capital-usd", "100000", "--interval", "1d",
         "--max-open-positions", "15", "--max-position-pct-of-capital", "5",
         "--placement-mode", "manual"]),
    "paper_equity_dryrun": (
        "tradepro_strategies.cli.paper_session",
        ["--strategy-id", "ichimoku_equity", "--from-config",
         "--placement-mode", "manual"]),
}


def _provenance() -> dict:
    """Which commit is running, and is that plausibly current.

    THE BUG THIS EXISTS FOR (31 Aug 2026). This function had a live EventBridge
    schedule — `tradepro-post-earnings-puts`, 20:45 UTC Mon-Fri, which PUSHES to
    the live desk artifact — but no CI deploy path. The image was uploaded by
    hand on 30 Aug; main then moved on twice. The scheduled job kept running
    30 Aug code, would have refused to price every weeknight, and reported that
    refusal in the STRATEGY's voice. Nothing said the deployment was stale.

    WHAT THIS DOES NOT DO, AND WHY (corrected same day). The first version
    compared this image's commit against the API's baked commit and WARNED on
    any difference. That warns permanently under normal operation: aws-build-push
    rebuilds the API only for `backend/` and `frontend/` changes, so every
    strategies-only commit legitimately leaves the two SHAs apart. Verified live
    — jobs at ebaae2d1, API at 1b125351, both correct and current.

    An alarm that fires when nothing is wrong is the cry-wolf failure this repo
    has now had to walk back three times (bar-cache tiers, the 5m harvest
    grading an unopened session, and this). So the API commit is REPORTED as
    context and never raises an alarm on its own.

    What genuinely indicates trouble, using only what the image can know:

      * UNSTAMPED    -> fail. Only a hand build produces this, which is exactly
                       what happened. Provenance is unknowable, not merely
                       different.
      * STALE BY AGE -> warn. The workflow deploys on every strategies/ push,
                       so an image that has not been rebuilt in weeks means the
                       pipeline itself stopped running. That is the condition
                       the original comparison was reaching for, expressed in a
                       way that cannot fire on a healthy day.
    """
    import datetime as _dt

    commit = (os.environ.get("JOBS_COMMIT") or "unknown").strip()
    built = (os.environ.get("JOBS_BUILD_TIME") or "unknown").strip()
    out = {"jobs_commit": commit[:12], "built": built, "api_commit": None,
           "age_days": None, "status": "ok", "detail": None}

    if commit in ("", "unknown"):
        out["status"] = "fail"
        out["detail"] = ("this image carries no JOBS_COMMIT, so it was not built by "
                         "aws-lambda-jobs — its provenance cannot be established")
        return out

    # Age, which is the only staleness signal the image can compute alone.
    if built not in ("", "unknown"):
        try:
            _b = _dt.datetime.fromisoformat(built.replace("Z", "+00:00"))
            if _b.tzinfo is None:
                _b = _b.replace(tzinfo=_dt.timezone.utc)
            age = (_dt.datetime.now(_dt.timezone.utc) - _b).days
            out["age_days"] = age
            if age > STALE_AFTER_DAYS:
                out["status"] = "warn"
                out["detail"] = (
                    f"this image was built {age} days ago ({built[:10]}). "
                    f"aws-lambda-jobs deploys on every strategies/ push, so an "
                    f"image older than {STALE_AFTER_DAYS} days means the deploy "
                    f"pipeline has stopped running")
        except Exception:  # noqa: BLE001 — an unparseable stamp is not an outage
            out["age_days"] = None

    # The API commit is CONTEXT, not a verdict. Recorded so a human comparing
    # two boards can see what each was produced against; never an alarm, because
    # the two pipelines deploy on different path filters by design.
    try:
        import requests
        base = os.environ.get("TRADEPRO_API_URL", "").rstrip("/")
        if base:
            r = requests.get(f"{base}/health/details", timeout=10)
            _j = r.json() or {}
            # NESTED under `deploy`, not at the root. Reading the root returned
            # None and the first version then reported "ok" having compared
            # nothing — a check that fails open certifies what it cannot see.
            api = ((_j.get("deploy") or {}).get("backendCommit")
                   or _j.get("gitSha") or "").strip()
            out["api_commit"] = api[:12] or None
    except (Exception, SystemExit):  # noqa: BLE001 — context must never break the job
        pass
    return out


def _report_provenance(job: str) -> dict:
    """Emit provenance to the central run log. Best-effort, never fatal."""
    prov = _provenance()
    try:
        from tradepro_strategies.run_log import log_run
        log_run(
            "lambda-jobs", "deploy", prov["status"],
            error=prov["detail"] if prov["status"] in ("warn", "fail") else None,
            summary=(f"job={job} image={prov['jobs_commit']} "
                     f"built={prov['built'][:10]} age={prov['age_days']}d "
                     f"api={prov['api_commit'] or 'unknown'}"),
        )
    except (Exception, SystemExit):  # noqa: BLE001 — logging must never fail the job
        # BOTH, and SystemExit is the one that matters. `except Exception` alone
        # took the whole Lambda down on 31 Aug 2026 — every scheduled job and
        # the 15-minute alerts — because log_run -> load_credentials() calls
        # sys.exit() when credentials are absent, and SystemExit derives from
        # BaseException, not Exception. It sailed through a guard whose own
        # docstring said "must never fail the job".
        #
        # Not BaseException: that would also swallow KeyboardInterrupt and
        # GeneratorExit, which should still propagate.
        #
        # The lesson generalises: anything in this codebase reaching
        # load_credentials can EXIT rather than raise, so a best-effort call
        # must name SystemExit explicitly or it is not best-effort.
        pass
    if prov["status"] != "ok":
        log.warning("DEPLOY PROVENANCE %s: %s", prov["status"].upper(), prov["detail"])
    return prov


def _run(job: str) -> dict:
    import importlib
    import sys
    if job not in JOBS:
        return {"ok": False, "error": f"unknown job {job!r}", "known": sorted(JOBS)}
    module, argv = JOBS[job]
    mod = importlib.import_module(module)
    old = sys.argv
    sys.argv = [job, *argv]
    try:
        rc = mod.main()
        return {"ok": rc == 0, "job": job, "rc": rc}
    finally:
        sys.argv = old


def handler(event, context):  # noqa: ANN001 — AWS signature
    """EventBridge passes {"job": "..."}; a UI invoke passes the same shape.

    HOME is repointed at /tmp because the package writes its ledger and
    caches under ~/.tradepro, and Lambda's filesystem is read-only apart from
    /tmp. That makes the ledger EPHEMERAL — fine for the alert, which only
    needs today's strikes, and NOT fine for the paper record, which is why that
    job pushes to the API rather than trusting local state.
    """
    os.environ.setdefault("HOME", "/tmp")
    os.makedirs("/tmp/.tradepro/research", exist_ok=True)

    job = (event or {}).get("job") or os.environ.get("TRADEPRO_JOB")
    if not job:
        return {"statusCode": 400,
                "body": json.dumps({"ok": False,
                                    "error": "no job specified",
                                    "known": sorted(JOBS),
                                    "provenance": _provenance()})}
    log.info("running job=%s", job)
    prov = _report_provenance(job)
    try:
        result = _run(job)
    except SystemExit as exc:
        # A JOB THAT EXITS MUST STILL EXPLAIN ITSELF (31 Aug 2026).
        #
        # Several CLIs call sys.exit() for an expected, well-described failure —
        # `push_to_api.load_credentials()` prints exactly which sources it
        # checked and then exits 2. Inside Lambda that terminated the runtime,
        # and the caller saw only:
        #
        #     {"errorType": "Runtime.ExitError",
        #      "errorMessage": "Error: Runtime exited with error: exit status 2"}
        #
        # with no traceback, because nothing raised. The real reason was sitting
        # in CloudWatch two lines earlier and nowhere else — not in the run log,
        # not in the invoke response, not in the UI that triggered it.
        #
        # SystemExit is not an Exception, so the handler below never saw it.
        log.error("job %s exited with status %s", job, exc.code)
        result = {"ok": False, "job": job, "rc": exc.code,
                  "error": (f"the job called sys.exit({exc.code}) — this is usually a "
                            f"missing credential or config; the CLI prints the specific "
                            f"reason to stderr immediately before exiting, see the "
                            f"CloudWatch line above this one")}
    except Exception as exc:  # noqa: BLE001 — a crash must return a READABLE reason
        log.error("job %s failed: %s\n%s", job, exc, traceback.format_exc())
        result = {"ok": False, "job": job, "error": str(exc)[:400]}
    # Provenance rides along on EVERY response, including the smoke test, so
    # "which code produced this board" is answerable without SSM or a redeploy.
    result["provenance"] = prov
    log.info("result: %s", result)
    return {"statusCode": 200 if result.get("ok") else 500,
            "body": json.dumps(result)}
