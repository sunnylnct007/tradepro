"""Steps for push_compare.feature — exercise the credentials loader
and the push helper without hitting the network."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from behave import given, then, when


def _set_home(context, home: Path, cred_path: Path) -> None:
    """Pin HOME so Path.home() resolves to the test fixture, not the
    real user home. The loader looks for ~/.tradepro/credentials so
    HOME must be the parent of the .tradepro dir, not the dir itself."""
    os.environ["HOME"] = str(home)
    context.cred_path = cred_path


@given('a credentials file with base "{base}" and token "{token}"')
def step_creds_file(context, base: str, token: str):
    root = Path(context.tmp_root) / "push_creds_test_with_file"
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                p.unlink()
    root.mkdir(parents=True, exist_ok=True)
    cred_dir = root / ".tradepro"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_path = cred_dir / "credentials"
    cred_path.write_text(json.dumps({
        "api_base_url": base, "api_token": token,
    }))
    _set_home(context, root, cred_path)
    # Clear env vars so file path is the only available source.
    os.environ.pop("TRADEPRO_API_URL", None)
    os.environ.pop("TRADEPRO_API_TOKEN", None)


@given("no credentials file")
def step_no_creds_file(context):
    root = Path(context.tmp_root) / "push_creds_test_no_file"
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                p.unlink()
    root.mkdir(parents=True, exist_ok=True)
    cred_dir = root / ".tradepro"
    cred_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately do NOT create the file.
    _set_home(context, root, cred_dir / "credentials")


@given('the env TRADEPRO_API_URL is "{url}" and TRADEPRO_API_TOKEN is "{token}"')
def step_env_set(context, url: str, token: str):
    os.environ["TRADEPRO_API_URL"] = url
    os.environ["TRADEPRO_API_TOKEN"] = token


@given("the env TRADEPRO_API_URL is unset and TRADEPRO_API_TOKEN is unset")
def step_env_unset(context):
    os.environ.pop("TRADEPRO_API_URL", None)
    os.environ.pop("TRADEPRO_API_TOKEN", None)


@when("I load push credentials")
def step_load(context):
    from tradepro_strategies.mcp.tools import _load_push_credentials
    context.base, context.token, context.source = _load_push_credentials()


@then('the loaded base is "{expected}"')
def step_assert_base(context, expected: str):
    assert context.base == expected, f"got {context.base!r}"


@then("the loaded base is None")
def step_assert_base_none(context):
    assert context.base is None, f"expected None, got {context.base!r}"


@then('the loaded token is "{expected}"')
def step_assert_token(context, expected: str):
    assert context.token == expected, f"got {context.token!r}"


@then("the loaded token is None")
def step_assert_token_none(context):
    assert context.token is None, f"expected None, got {context.token!r}"


@then('the loaded source is "{expected}"')
def step_assert_source(context, expected: str):
    assert context.source == expected, f"got {context.source!r}"


@when("I push a synthetic compare payload")
def step_push(context):
    from tradepro_strategies.mcp.tools import _push_compare
    payload = {"universe": "test", "rows": [{"symbol": "TEST"}]}
    # Even when creds are missing the helper must not call requests
    # (otherwise we'd need to mock the network here). It returns
    # skipped=True before the POST.
    with patch("tradepro_strategies.mcp.tools.requests.post") as mock_post:
        context.push_result = _push_compare(payload)
        context.post_called = mock_post.called


@then("the push result is skipped with a clear reason")
def step_assert_skipped(context):
    assert context.push_result.get("skipped") is True
    reason = context.push_result.get("reason") or ""
    assert "credentials" in reason.lower(), reason
    assert not context.post_called, "_push_compare hit the network without creds"


@then("the push result is not ok")
def step_assert_not_ok(context):
    assert context.push_result.get("ok") is False
