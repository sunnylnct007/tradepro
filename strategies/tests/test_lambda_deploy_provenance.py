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

Four states, and the two that matter most are the ones where we CANNOT tell:

  * stamped and matching        -> ok
  * stamped and different       -> warn   (persistent = stale; brief = rollout)
  * unstamped                   -> fail   (hand-built, provenance unknowable)
  * cannot read the API commit  -> warn   (NOT ok — see below)

The first version of this check read `backendCommit` off the root of
/health/details, where it does not live — it is nested under `deploy`. That
returned None, the comparison was skipped, and the status stayed "ok". A drift
alarm that fails open is worse than no alarm, because it certifies precisely
the thing it cannot see. Hence the explicit unreadable-commit cases here.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lambda_handler as H  # noqa: E402

API = "http://api.test"
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


def test_matching_commits_are_ok(env):
    env.setenv("JOBS_COMMIT", SHA)
    _patch(env, {"deploy": {"backendCommit": SHA}})
    p = H._provenance()
    assert p["status"] == "ok", p
    assert p["jobs_commit"] == SHA[:12]
    assert p["api_commit"] == SHA[:12]


def test_a_stale_image_warns_and_names_both_commits(env):
    """THE regression. A job running yesterday's code must say so, and say what
    it is running versus what it should be."""
    env.setenv("JOBS_COMMIT", "deadbeef1234567890")
    _patch(env, {"deploy": {"backendCommit": SHA}})
    p = H._provenance()
    assert p["status"] == "warn", p
    assert "deadbeef1234" in p["detail"] and SHA[:12] in p["detail"], p["detail"]


def test_an_unstamped_image_fails(env):
    """Only a hand build produces this. It is the exact thing that happened, and
    it is unknowable rather than merely different — so it is a FAIL, not a warn."""
    env.delenv("JOBS_COMMIT", raising=False)
    p = H._provenance()
    assert p["status"] == "fail", p
    assert "not built by" in p["detail"]


def test_commit_nested_under_deploy_is_actually_read(env):
    """`backendCommit` is nested, not top-level. Reading the root returned None,
    which silently skipped the comparison — the alarm's own fail-open bug."""
    env.setenv("JOBS_COMMIT", SHA)
    _patch(env, {"deploy": {"backendCommit": SHA}, "gitSha": SHA})
    assert H._provenance()["api_commit"] == SHA[:12]


def test_missing_api_commit_warns_rather_than_certifying_ok(env):
    """If the API will not say what it is running, drift CANNOT be checked. That
    must never read as agreement."""
    env.setenv("JOBS_COMMIT", SHA)
    _patch(env, {"deploy": {}})
    p = H._provenance()
    assert p["status"] == "warn", p
    assert "cannot be checked" in p["detail"]


def test_unreachable_api_warns(env):
    env.setenv("JOBS_COMMIT", SHA)
    _patch(env, boom=ConnectionError("down"))
    p = H._provenance()
    assert p["status"] == "warn", p
    assert p["api_commit"] is None


def test_no_api_url_warns(env):
    env.delenv("TRADEPRO_API_URL", raising=False)
    env.setenv("JOBS_COMMIT", SHA)
    p = H._provenance()
    assert p["status"] == "warn", p


def test_every_known_job_is_still_registered():
    """The smoke test in aws-lambda-jobs asserts this list; keep them honest
    about each other."""
    assert "post_earnings_puts" in H.JOBS
    for job, (module, argv) in H.JOBS.items():
        assert module.startswith("tradepro_strategies."), job
        assert isinstance(argv, list), job
