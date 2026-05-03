"""Steps for observability.feature — locks in the auto-trace contract.

Tests reset module state on the SessionTrace singleton between
scenarios so each starts with a clean trace file in a tmp dir.
"""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

from behave import given, then, when


def _reset_session(context, trace_root: Path, *, full: bool = False) -> None:
    # Override the trace root so we don't pollute ~/.tradepro/traces.
    os.environ["HOME"] = str(trace_root.parent)
    os.environ["TRADEPRO_TRACE_FULL"] = "1" if full else ""
    # Reload modules so the new HOME and env are picked up.
    from tradepro_strategies.mcp import trace as trace_mod
    from tradepro_strategies.mcp import session as session_mod
    importlib.reload(trace_mod)
    importlib.reload(session_mod)
    context.session_mod = session_mod
    context.trace_mod = trace_mod
    # Force a fresh singleton.
    session_mod._SESSION = None


@given("a fresh session trace")
def step_fresh_session(context):
    root = Path(context.tmp_root) / "obs_test"
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                p.unlink()
    root.mkdir(parents=True, exist_ok=True)
    _reset_session(context, root, full=False)


@given("a fresh session trace with TRADEPRO_TRACE_FULL=1")
def step_fresh_session_full(context):
    root = Path(context.tmp_root) / "obs_test_full"
    if root.exists():
        for p in root.rglob("*"):
            if p.is_file():
                p.unlink()
    root.mkdir(parents=True, exist_ok=True)
    _reset_session(context, root, full=True)


@when("I call an instrumented tool that returns a JSON _source citation")
def step_call_ok(context):
    instrumented = context.session_mod.instrumented

    @instrumented("fake_tool")
    def fake_tool(symbol: str) -> str:
        return json.dumps({
            "_source": f"tradepro://test/{symbol}",
            "ok": True,
            "symbol": symbol,
            "value": 42,
        })

    context.result = fake_tool("QQQ")


@when("I call an instrumented tool that raises an exception")
def step_call_raise(context):
    instrumented = context.session_mod.instrumented

    @instrumented("boom_tool")
    def boom_tool() -> str:
        raise RuntimeError("kaboom")

    try:
        boom_tool()
        context.propagated = False
    except RuntimeError:
        context.propagated = True


@when("I call an instrumented tool that returns a large payload")
def step_call_large(context):
    instrumented = context.session_mod.instrumented

    @instrumented("big_tool")
    def big_tool() -> str:
        # Big enough to trigger truncation in summary mode.
        return json.dumps({
            "_source": "tradepro://big",
            "rows": [{"i": i, "filler": "x" * 50} for i in range(100)],
        })

    context.result = big_tool()


@then("the session trace gains one tool_call step")
def step_one_step(context):
    s = context.session_mod.session()
    assert len(s.steps) == 1, f"expected 1 step, got {len(s.steps)}"
    assert s.steps[0]["kind"] == "tool_call"


@then("the recorded step captures the latency and the citation _source")
def step_latency_and_source(context):
    s = context.session_mod.session()
    step = s.steps[0]
    assert isinstance(step["latency_ms"], int) and step["latency_ms"] >= 0
    out = step["outputs"]
    assert isinstance(out, dict), f"outputs not a dict: {out!r}"
    assert out.get("_source", "").startswith("tradepro://test/"), out


@then("the recorded step has no error")
def step_no_error(context):
    assert context.session_mod.session().steps[0]["error"] is None


@then("the recorded step has an error matching the exception")
def step_has_error(context):
    err = context.session_mod.session().steps[0]["error"]
    assert err is not None and "kaboom" in err, err


@then("the original exception still propagates to the caller")
def step_propagated(context):
    assert context.propagated is True


@then("the recorded step retains the full parsed payload")
def step_full_payload(context):
    out = context.session_mod.session().steps[0]["outputs"]
    # Full mode keeps the raw parsed payload, including the rows array.
    assert isinstance(out, dict)
    assert "rows" in out and len(out["rows"]) == 100
