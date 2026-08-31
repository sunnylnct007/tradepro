"""A best-effort log line must never be able to kill its caller.

THE DEFECT, 31 Aug 2026. `log_runs` carries the comment "never let logging break
the op" and wraps everything in `except Exception`. That does not deliver the
contract: when it has no explicit base/token it calls
`push_to_api.load_credentials()`, which prints a message and calls `sys.exit(2)`
if the api-base-url/api-token pair cannot be resolved from env, Secrets Manager
or ~/.tradepro. `SystemExit` derives from BaseException, NOT Exception, so it
sailed straight through the handler.

On any host without credentials, a fire-and-forget telemetry call therefore
terminated the process that made it.

Observed on the tradepro-jobs Lambda: the function died with
`Runtime.ExitError: exit status 2` immediately after logging "running job=...".
There was no traceback anywhere, because nothing raised — the process exited.
Every scheduled job on that function would have crashed the same way, including
the 20:45 puts screen that pushes to the live desk.

The bug was introduced by a deploy-provenance alarm written to make silent
staleness loud. It made the function fail instead. The smoke test in
aws-lambda-jobs caught it before any scheduled run — which is the only reason
this is a test and not an outage.

KeyboardInterrupt is deliberately still allowed to propagate: a human
interrupting a run must be able to stop it.
"""
from __future__ import annotations

import pytest

from tradepro_strategies import run_log


def _force_credential_exit(monkeypatch):
    """Make the credential lookup do what it really does when unset: exit."""
    import sys

    from tradepro_strategies.cli import push_to_api

    def _exit(*a, **kw):
        print("credentials must include api-base-url and api-token", file=sys.stderr)
        raise SystemExit(2)

    monkeypatch.setattr(push_to_api, "load_credentials", _exit)


def test_missing_credentials_do_not_kill_the_caller(monkeypatch):
    """THE regression. This used to terminate the process with status 2."""
    _force_credential_exit(monkeypatch)
    survived = False
    delivered = run_log.log_run("unit-test", "probe", "ok", summary="x")
    survived = True          # unreachable if SystemExit escapes
    assert survived
    assert delivered is False, "a failed delivery must report False, not raise"


def test_the_caller_keeps_running_afterwards(monkeypatch):
    """Not merely 'does not raise' — the code AFTER the log call must run. The
    Lambda died between logging the job name and doing the job."""
    _force_credential_exit(monkeypatch)
    steps = []
    steps.append("before")
    run_log.log_run("unit-test", "probe", "warn", error="boom")
    steps.append("after")
    assert steps == ["before", "after"], steps


def test_ordinary_exceptions_are_still_swallowed(monkeypatch):
    """The original contract must not regress while widening it."""
    from tradepro_strategies.cli import push_to_api
    monkeypatch.setattr(
        push_to_api, "load_credentials",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("network gone")))
    assert run_log.log_run("unit-test", "probe", "ok") is False


def test_keyboard_interrupt_still_propagates(monkeypatch):
    """A human stopping a run must not be swallowed by telemetry."""
    from tradepro_strategies.cli import push_to_api
    monkeypatch.setattr(
        push_to_api, "load_credentials",
        lambda *a, **kw: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        run_log.log_run("unit-test", "probe", "ok")
