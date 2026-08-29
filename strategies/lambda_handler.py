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
                                    "known": sorted(JOBS)})}
    log.info("running job=%s", job)
    try:
        result = _run(job)
    except Exception as exc:  # noqa: BLE001 — a crash must return a READABLE reason
        log.error("job %s failed: %s\n%s", job, exc, traceback.format_exc())
        result = {"ok": False, "job": job, "error": str(exc)[:400]}
    log.info("result: %s", result)
    return {"statusCode": 200 if result.get("ok") else 500,
            "body": json.dumps(result)}
