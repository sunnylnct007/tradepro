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

# job name -> (module, argv). Nothing here may need local disk beyond /tmp.
JOBS: dict[str, tuple[str, list[str]]] = {
    "index_strangle_paper": ("tradepro_strategies.cli.index_strangle_paper", ["--email"]),
    "index_strangle_alert": ("tradepro_strategies.cli.index_strangle_alert", ["--email"]),
    "post_earnings_puts":   ("tradepro_strategies.cli.post_earnings_puts", ["--push"]),
}


def _provenance() -> dict:
    """Which commit is running, and does it match what the API is running.

    THE BUG THIS EXISTS FOR (31 Aug 2026). This function had a live EventBridge
    schedule — `tradepro-post-earnings-puts`, 20:45 UTC Mon-Fri, which PUSHES to
    the live desk artifact — but no CI deploy path. The image was uploaded by
    hand on 30 Aug; `main` then moved on twice. The scheduled job kept running
    30 Aug code, would have refused to price every weeknight, and reported that
    refusal in the STRATEGY's voice ("no expiry near the target"). Nothing said
    the deployment was stale. It was found by going looking, which is not a
    control.

    The owner's rule is that every integration fails LOUD. A deploy that can
    silently diverge from main is the one integration that had no voice at all.

    THE COMPARISON is against the API's own baked commit, because both are
    deployed from main and the Lambda has no git. Not exact — the two pipelines
    fire on different triggers, so a brief mismatch during a rollout is normal.
    That is why a mismatch is a WARN and not a failure: a persistent one is the
    bug, a momentary one is a deploy in flight. An UNSTAMPED image is a FAIL,
    because it can only come from a hand build and its provenance is unknowable.
    """
    commit = (os.environ.get("JOBS_COMMIT") or "unknown").strip()
    out = {"jobs_commit": commit[:12], "api_commit": None, "status": "ok", "detail": None}
    if commit in ("", "unknown"):
        out["status"] = "fail"
        out["detail"] = ("this image carries no JOBS_COMMIT, so it was not built by "
                         "aws-lambda-jobs — its provenance cannot be established")
        return out
    try:
        import requests
        base = os.environ.get("TRADEPRO_API_URL", "").rstrip("/")
        if not base:
            out["status"] = "warn"
            out["detail"] = "TRADEPRO_API_URL unset — cannot compare against the API"
            return out
        r = requests.get(f"{base}/health/details", timeout=10)
        _j = r.json() or {}
        # NESTED, not top-level. Reading `backendCommit` off the root returns
        # None, and the first version of this check then skipped the comparison
        # and still reported "ok" — a drift alarm that fails open is worse than
        # none, because it certifies exactly what it cannot see.
        api = ((_j.get("deploy") or {}).get("backendCommit")
               or _j.get("gitSha") or "").strip()
        out["api_commit"] = api[:12] or None
        if not api:
            out["status"] = "warn"
            out["detail"] = ("the API reported no commit, so drift cannot be "
                             "checked — treat this run's provenance as unknown")
        elif not api.startswith(commit[:12]) and not commit.startswith(api[:12]):
            out["status"] = "warn"
            out["detail"] = (f"jobs image is at {commit[:12]} while the API is at "
                             f"{api[:12]} — if this persists past a rollout the "
                             f"scheduled jobs are running stale code")
    except Exception as exc:  # noqa: BLE001 — provenance must never break the job
        out["status"] = "warn"
        out["detail"] = f"could not read the API commit: {str(exc)[:120]}"
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
                     f"api={prov['api_commit'] or 'unknown'}"),
        )
    except Exception:  # noqa: BLE001 — logging must never fail the job
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
    except Exception as exc:  # noqa: BLE001 — a crash must return a READABLE reason
        log.error("job %s failed: %s\n%s", job, exc, traceback.format_exc())
        result = {"ok": False, "job": job, "error": str(exc)[:400]}
    # Provenance rides along on EVERY response, including the smoke test, so
    # "which code produced this board" is answerable without SSM or a redeploy.
    result["provenance"] = prov
    log.info("result: %s", result)
    return {"statusCode": 200 if result.get("ok") else 500,
            "body": json.dumps(result)}
