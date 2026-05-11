"""Steps for s3_archive.feature — covers the gating + key-shape contract
of `_maybe_archive_to_s3` / `_archive_object_key` in push_to_api.

We never hit real S3 from tests. The "no upload attempted" assertion
works by setting TRADEPRO_S3_ARCHIVE empty + patching boto3 to None;
both layers must be off for the function to silently return."""
from __future__ import annotations

import builtins
import io
import os
import sys
from contextlib import redirect_stderr

from behave import given, then, when

from tradepro_strategies.cli.push_to_api import (
    _archive_object_key,
    _maybe_archive_to_s3,
)


@given("the TRADEPRO_S3_ARCHIVE env is unset")
def step_env_unset(context) -> None:
    os.environ.pop("TRADEPRO_S3_ARCHIVE", None)


@given('the TRADEPRO_S3_ARCHIVE env is "{value}"')
def step_env_set(context, value: str) -> None:
    os.environ["TRADEPRO_S3_ARCHIVE"] = value
    # Make sure the test cleans up so other features aren't affected.
    context.add_cleanup(lambda: os.environ.pop("TRADEPRO_S3_ARCHIVE", None))


@given("boto3 is not importable")
def step_boto3_unavailable(context) -> None:
    # Hide boto3 via a temporary import override. Restore after the
    # scenario so the rest of the suite keeps working if it happens
    # to be installed in a future environment.
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3" or name.startswith("boto3."):
            raise ImportError("boto3 hidden by test fixture")
        return original_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    # Also remove any cached module so the next import re-runs the hook.
    sys.modules.pop("boto3", None)
    context.add_cleanup(lambda: setattr(builtins, "__import__", original_import))


@when("I call _maybe_archive_to_s3 with a compare payload")
def step_call_archive(context) -> None:
    payload = {"universe": "etf_us_core", "run_id": "test-run", "rows": []}
    err = io.StringIO()
    with redirect_stderr(err):
        _maybe_archive_to_s3("compare", payload)
    context.stderr = err.getvalue()


@then("no S3 upload is attempted")
def step_no_upload(context) -> None:
    # When TRADEPRO_S3_ARCHIVE is unset, the function returns before
    # touching boto3 — so nothing is logged to stderr. Treat a clean
    # stderr as proof that the early-return fired.
    assert context.stderr == "", (
        f"expected silent no-op, got stderr={context.stderr!r}"
    )


@then('stderr mentions "{snippet}"')
def step_stderr_mentions(context, snippet: str) -> None:
    assert snippet in context.stderr, (
        f"expected stderr to mention {snippet!r}, got {context.stderr!r}"
    )


# ----- key-shape contracts -----

@given('a compare payload for universe "{u}" with run_id "{rid}"')
def step_compare_with_run(context, u: str, rid: str) -> None:
    context.kind = "compare"
    context.payload = {"universe": u, "run_id": rid}


@given('a compare payload for universe "{u}" with no run_id')
def step_compare_no_run(context, u: str) -> None:
    context.kind = "compare"
    context.payload = {"universe": u}


@given('a heartbeat payload from host "{h}"')
def step_heartbeat(context, h: str) -> None:
    context.kind = "heartbeat"
    context.payload = {"host": h}


@when("I build the S3 archive key")
def step_build_key(context) -> None:
    context.key = _archive_object_key(context.kind, context.payload)


@then('the key equals "{expected}"')
def step_key_equals(context, expected: str) -> None:
    assert context.key == expected, (
        f"expected key {expected!r}, got {context.key!r}"
    )


@then('the key starts with "{prefix}"')
def step_key_starts(context, prefix: str) -> None:
    assert context.key.startswith(prefix), (
        f"expected key to start with {prefix!r}, got {context.key!r}"
    )


@then('the key ends with "{suffix}"')
def step_key_ends(context, suffix: str) -> None:
    assert context.key.endswith(suffix), (
        f"expected key to end with {suffix!r}, got {context.key!r}"
    )
