"""Steps for llm_health.feature — exercise OllamaProvider's
health_summary by stubbing the network layer."""
from __future__ import annotations

from unittest.mock import patch

import requests
from behave import given, then, when

from tradepro_strategies.llm.ollama_provider import OllamaProvider


class _FakeResp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


@given("an Ollama provider that cannot reach the host")
def step_daemon_down(context):
    context.provider = OllamaProvider(model="llama3.1:8b")
    context.patch_get = patch(
        "tradepro_strategies.llm.ollama_provider.requests.get",
        side_effect=requests.ConnectionError("nope"),
    )
    context.patch_get.start()


@given('an Ollama provider with daemon up but model "{model}" not pulled')
def step_model_missing(context, model: str):
    context.provider = OllamaProvider(model=model)
    # Ollama returns 200 with a list that does NOT include the model.
    other_models = [{"name": "qwen2.5-coder:1.5b-base"}, {"name": "mxbai-embed-large:latest"}]
    context.patch_get = patch(
        "tradepro_strategies.llm.ollama_provider.requests.get",
        return_value=_FakeResp(200, {"models": other_models}),
    )
    context.patch_get.start()


@given('an Ollama provider with daemon up and model "{model}" available')
def step_model_present(context, model: str):
    context.provider = OllamaProvider(model=model)
    context.patch_get = patch(
        "tradepro_strategies.llm.ollama_provider.requests.get",
        return_value=_FakeResp(200, {"models": [{"name": model}]}),
    )
    context.patch_get.start()


@when("I get the health summary")
def step_get_summary(context):
    try:
        context.summary = context.provider.health_summary()
    finally:
        context.patch_get.stop()


@then('the state is "{expected}"')
def step_assert_state(context, expected: str):
    assert context.summary["state"] == expected, context.summary


@then("the message tells the user how to start Ollama")
def step_msg_start(context):
    msg = context.summary.get("message", "")
    assert "ollama serve" in msg.lower() or "start" in msg.lower(), msg


@then('the message tells the user to "{snippet}"')
def step_msg_pull(context, snippet: str):
    msg = context.summary.get("message", "")
    assert snippet in msg, f"message does not contain {snippet!r}: {msg!r}"


@then("ok is {expected}")
def step_assert_ok(context, expected: str):
    expected_bool = {"True": True, "False": False}[expected]
    assert context.summary["ok"] is expected_bool, context.summary
