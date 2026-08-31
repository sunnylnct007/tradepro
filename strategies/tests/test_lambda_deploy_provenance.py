"""A scheduled job must be able to say WHICH COMMIT produced its output.

THE DEFECT, 31 Aug 2026. `tradepro-jobs` had a live EventBridge schedule —
`tradepro-post-earnings-puts`, 20:45 UTC Mon-Fri, which PUSHES to the live desk
artifact — and no CI deploy path at all. The image was uploaded by hand on
30 Aug; main then moved on twice. The scheduled job kept running 30 Aug code,
would have refused to price every weeknight, and reported that refusal in the
STRATEGY's voice ("no expiry near the target").

Nothing reported the deployment was stale. It was found by going looking, which
is not a control. The owner's standing rule is that every integration fails
LOUD; a deploy able to silently diverge from main was the one integration with
no voice at all.

CORRECTED THE SAME DAY. The first version compared this image's commit against
the API's and WARNED on any difference. That warns permanently under normal
operation — aws-build-push rebuilds the API only for backend/ and frontend/
changes, so every strategies-only commit legitimately leaves the two apart
(verified live: jobs ebaae2d1, API 1b125351, both correct). An alarm that fires
when nothing is wrong is the cry-wolf failure this repo has walked back three
times. The API commit is now CONTEXT, never a verdict.

What is asserted instead, using only what the image can know alone:

  * unstamped        -> FAIL   (hand-built; provenance unknowable)
  * stale by age     -> warn   (the deploy pipeline itself stopped running)
  * fresh            -> ok     (even when the API commit differs)

"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_handler as H  # noqa: E402

API = "http://api.test"


def _now_iso() -> str:
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _iso_days_ago(n: int) -> str:
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)).isoformat()
SHA = "1b125351f9f18bb58bcc65c002558d26acfdf80b"


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _patch(mp, payload=None, boom=None):
    """Stub requests.get for the provenance probe only."""
    import requests

    def fake_get(url, timeout=None, **kw):
        if boom is not None:
            raise boom
        return _Resp(payload)

    mp.setattr(requests, "get", fake_get)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TRADEPRO_API_URL", API)
    return monkeypatch


def test_a_fresh_image_is_ok_even_when_the_api_commit_differs(env):
    """THE correction. A strategies-only push leaves the API behind BY DESIGN.
    That must not raise anything."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, {"deploy": {"backendCommit": "1b125351f9f18bb"}})
    p = H._provenance()
    assert p["status"] == "ok", p
    assert p["api_commit"] == "1b125351f9f1", "the API commit is still RECORDED"
    assert p["age_days"] == 0


def test_an_image_older_than_the_threshold_warns(env):
    """The signal the comparison was reaching for, expressed so it cannot fire
    on a healthy day: the workflow deploys on every strategies/ push, so a stale
    image means the pipeline stopped."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _iso_days_ago(H.STALE_AFTER_DAYS + 10))
    _patch(env, {"deploy": {"backendCommit": SHA}})
    p = H._provenance()
    assert p["status"] == "warn", p
    assert "deploy pipeline has stopped" in p["detail"], p["detail"]


def test_an_image_just_inside_the_threshold_is_ok(env):
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _iso_days_ago(H.STALE_AFTER_DAYS - 2))
    _patch(env, {"deploy": {"backendCommit": SHA}})
    assert H._provenance()["status"] == "ok"


def test_an_unstamped_image_fails(env):
    """Only a hand build produces this. It is the exact thing that happened, and
    it is unknowable rather than merely different — so it is a FAIL."""
    env.delenv("JOBS_COMMIT", raising=False)
    p = H._provenance()
    assert p["status"] == "fail", p
    assert "not built by" in p["detail"]


def test_commit_nested_under_deploy_is_actually_read(env):
    """`backendCommit` is nested, not top-level. Reading the root returned None,
    and the first version reported "ok" having compared nothing."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, {"deploy": {"backendCommit": SHA}, "gitSha": SHA})
    assert H._provenance()["api_commit"] == SHA[:12]


def test_an_unreachable_api_is_not_an_alarm(env):
    """Context that cannot be fetched is missing context, not a fault — the job
    itself is unaffected by it."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, boom=ConnectionError("down"))
    p = H._provenance()
    assert p["status"] == "ok", p
    assert p["api_commit"] is None


def test_a_credential_exit_in_the_context_probe_does_not_kill_the_job(env):
    """SystemExit is not an Exception. `load_credentials` exits rather than
    raising, and that is how the first version of this alarm crashed the very
    function it was written to protect."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, boom=SystemExit(2))
    assert H._provenance()["status"] == "ok"


def test_every_known_job_is_still_registered():
    """The smoke test in aws-lambda-jobs asserts this list; keep them honest
    about each other."""
    assert "post_earnings_puts" in H.JOBS
    for job, (module, argv) in H.JOBS.items():
        assert module.startswith("tradepro_strategies."), job
        assert isinstance(argv, list), job


def test_a_job_that_exits_returns_a_readable_error_not_a_runtime_crash(env, monkeypatch):
    """THE regression, 31 Aug 2026. `post_earnings_puts` calls
    `load_credentials()`, which prints the sources it checked and then exits 2
    when none has the api-base-url/api-token pair. Inside Lambda that terminated
    the runtime and the caller saw only:

        {"errorType": "Runtime.ExitError",
         "errorMessage": "Error: Runtime exited with error: exit status 2"}

    No traceback, because nothing raised. The actual reason existed only in
    CloudWatch — not in the run log, not in the invoke response, not in the UI
    that triggered the job.
    """
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, {"deploy": {"backendCommit": SHA}})

    def _exits(job):
        raise SystemExit(2)

    monkeypatch.setattr(H, "_run", _exits)
    resp = H.handler({"job": "post_earnings_puts"}, None)
    import json as _json
    body = _json.loads(resp["body"])
    assert resp["statusCode"] == 500
    assert body["ok"] is False
    assert body["rc"] == 2
    assert "sys.exit(2)" in body["error"], body["error"]
    assert "credential" in body["error"], body["error"]


def test_an_ordinary_job_crash_still_reports_its_message(env, monkeypatch):
    """Widening to SystemExit must not swallow the normal path."""
    env.setenv("JOBS_COMMIT", SHA)
    env.setenv("JOBS_BUILD_TIME", _now_iso())
    _patch(env, {"deploy": {"backendCommit": SHA}})

    def _raises(job):
        raise RuntimeError("chain unavailable")

    monkeypatch.setattr(H, "_run", _raises)
    import json as _json
    body = _json.loads(H.handler({"job": "post_earnings_puts"}, None)["body"])
    assert body["ok"] is False
    assert "chain unavailable" in body["error"]
