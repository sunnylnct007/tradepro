"""desk_check exits 1 when it finds problems. That is it WORKING.

Seen live 22 Sep 2026, alongside a real bars_5m fault:

    [BROKEN] Job · desk-check: last run exited 1

The check reads launchd exit codes for the jobs it watches, and it watches
ITSELF — deliberately, because a dead checker is the worst failure in this file:
indistinguishable from a healthy desk unless something says otherwise.

But `return 1 if any(c.bad ...)` is its contract with cron. So the moment
anything else broke, it exited 1, then read its own exit code as a job failure,
added a second BROKEN lane that did not exist, and kept the verdict bad — which
kept the exit code at 1. A self-sustaining alarm saying nothing about the desk.

The watch stays. Only the reading of its own exit 1 changes.
"""
import ast
import pathlib

SRC = (pathlib.Path(__file__).resolve().parents[1]
       / "tradepro_strategies" / "cli" / "desk_check.py").read_text()


def _check_jobs_src() -> str:
    for n in ast.walk(ast.parse(SRC)):
        if isinstance(n, ast.FunctionDef) and n.name == "check_jobs":
            return ast.get_source_segment(SRC, n) or ""
    raise AssertionError("check_jobs not found")


def test_it_still_watches_itself():
    """Removing the self-watch would be the wrong fix — a dead checker is
    silent, and silence is indistinguishable from a healthy desk."""
    assert '"com.tradepro.desk-check",' in SRC
    assert "SELF_JOB" in SRC


def test_its_own_exit_1_is_not_a_fault():
    b = _check_jobs_src()
    assert 'job == SELF_JOB and code == "1"' in b
    # Bound by the BRANCH, not a byte count. A fixed window here silently
    # stopped covering the assertion the moment the explaining comment grew —
    # the fourth time a slice-by-guess has been the wrong tool in this repo.
    i = b.index('job == SELF_JOB')
    j = b.index('elif code not in ("0", "-")', i)
    branch = b[i:j]
    assert "OK," in branch, "its own exit 1 must not produce a BROKEN lane"
    assert "BROKEN" not in branch.split("out.append")[1], (
        "the self-case appends a BROKEN check"
    )


def test_a_CRASH_still_fails():
    """Only 0 and 1 are contractual. A kill (137) or a traceback (2) is real."""
    b = _check_jobs_src()
    # the generic non-zero branch survives after the self-case
    assert 'elif code not in ("0", "-")' in b
    assert b.index('job == SELF_JOB') < b.index('elif code not in ("0", "-")'), (
        "the self-case must come FIRST or the generic branch catches exit 1"
    )


def test_NOT_LOADED_still_fails_for_itself_too():
    """The failure the self-watch exists for: it is not scheduled at all."""
    b = _check_jobs_src()
    assert "not loaded in launchd" in b
    assert b.index("if code is None") < b.index("job == SELF_JOB"), (
        "not-loaded must be checked before the exit-code cases"
    )


def test_ANOTHER_job_exiting_1_is_still_broken():
    """The exemption is for THIS process only, not a blanket 'exit 1 is fine'."""
    b = _check_jobs_src()
    i = b.index('job == SELF_JOB')
    assert 'code == "1"' in b[i:i + 80], "the exemption must be narrow"
