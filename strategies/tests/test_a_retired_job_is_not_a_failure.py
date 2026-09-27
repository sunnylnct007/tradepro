"""Retiring a job must not create a daily failure.

Deleting a job from JOBS does NOT delete its EventBridge rule. The rule keeps
firing, the handler answers "unknown job", and the retirement becomes a red
line in the run log every single day — a false alarm manufactured while
tidying up. This desk has spent the week removing exactly that shape.

A retired job is HANDLED: it returns ok=True, says it is retired, and says why.
When the rule is finally removed, the entry goes with it.
"""
from __future__ import annotations

import lambda_handler as LH


def test_a_retired_job_returns_ok_and_explains_itself():
    for job, reason in LH.RETIRED_JOBS.items():
        r = LH._run(job)
        assert r["ok"] is True, (
            f"{job} is retired, not broken — reporting a retirement as a "
            "failure trains people to ignore failures")
        assert r.get("retired") is True
        assert r.get("reason"), f"{job} must say WHY it was retired"
        assert len(reason) > 40, "a one-word reason is not a reason"


def test_a_retired_job_is_no_longer_runnable():
    """The point is that it stops doing the thing, not just that it logs."""
    for job in LH.RETIRED_JOBS:
        assert job not in LH.JOBS, (
            f"{job} is listed as retired AND still in JOBS — it would run")


def test_ichimoku_no_longer_proposes_into_the_t212_book():
    """The reason this retirement exists: T212 is being handed to momentum,
    which needs a book nothing else proposes into."""
    for name, (module, argv) in LH.JOBS.items():
        if "paper_session" not in module:
            continue
        joined = " ".join(argv)
        assert "ichimoku_equity" not in joined, (
            f"job {name!r} still runs a paper session for ichimoku_equity: "
            f"{joined}")


def test_an_unknown_job_still_fails_loudly():
    """The guard must not become a hole — a genuine typo must still be an
    error, and must list both what runs and what was retired."""
    r = LH._run("no_such_job_at_all")
    assert r["ok"] is False
    assert "known" in r and "retired" in r, (
        "an unknown job must show BOTH lists — otherwise a retired job looks "
        "like a typo and a typo looks like a retirement")
