"""Steps for llm_gate_catalyst_overlay.feature — exercises the
Phase C-3.1 catalyst overlay on LLMSignalGate. The gate is run
with cfg.enabled=False so the sentiment path is bypassed; the
catalyst overlay is what we're pinning here.

(A separate suite already covers sentiment-path behaviour. This
file only asserts the C-3.1 layer on top.)"""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.paper.llm_gate import (
    CatalystHint,
    GateDecision,
    LLMGateConfig,
    LLMSignalGate,
)


@given('an LLMSignalGate with the gate disabled')
def step_gate_disabled(context):
    context.gate = LLMSignalGate(LLMGateConfig(enabled=False))
    context.catalysts = []


@given('a CatalystHint id {cid:d} kind "{kind}" severity "{severity}" '
       'days_until {days}')
def step_add_catalyst_hint(context, cid, kind, severity, days):
    # `days` arrives as a raw token so we can encode None as the literal
    # string "None" (matching the feature file ergonomics).
    days_until: int | None
    if days.strip() == "None":
        days_until = None
    else:
        days_until = int(days)
    if not hasattr(context, "catalysts"):
        context.catalysts = []
    context.catalysts.append(CatalystHint(
        id=cid,
        kind=kind,
        occurs_on="2026-06-13",   # placeholder — flag math uses days_until
        severity=severity,
        days_until=days_until,
    ))


@when('evaluate runs with signal {signal:f} and no catalysts')
def step_evaluate_no_catalysts(context, signal):
    context.decision = context.gate.evaluate("TEST", signal=signal, catalysts=None)


@when('evaluate runs with signal {signal:f} and the catalyst')
def step_evaluate_one_catalyst(context, signal):
    context.decision = context.gate.evaluate(
        "TEST", signal=signal, catalysts=context.catalysts,
    )


@when('evaluate runs with signal {signal:f} and the catalysts')
def step_evaluate_many_catalysts(context, signal):
    context.decision = context.gate.evaluate(
        "TEST", signal=signal, catalysts=context.catalysts,
    )


@then('the overlay flag is None')
def step_assert_flag_none(context):
    assert context.decision.catalyst_flag is None, (
        f"expected None, got {context.decision.catalyst_flag!r}"
    )


@then('the overlay flag is "{expected}"')
def step_assert_flag(context, expected):
    assert context.decision.catalyst_flag == expected, (
        f"expected {expected!r}, got {context.decision.catalyst_flag!r}"
    )


@then('the overlay scale_factor is {value:f}')
def step_assert_scale(context, value):
    actual = context.decision.scale_factor
    assert abs(actual - value) < 1e-6, (
        f"expected scale_factor={value}, got {actual}"
    )


@then('the overlay action is "{expected}"')
def step_assert_action(context, expected):
    assert context.decision.action == expected, (
        f"expected action={expected!r}, got {context.decision.action!r}"
    )


@then('the catalyst_ids list is empty')
def step_assert_ids_empty(context):
    assert context.decision.catalyst_ids == (), (
        f"expected empty tuple, got {context.decision.catalyst_ids!r}"
    )


@then('the catalyst_ids list contains {cid:d}')
def step_assert_ids_contains(context, cid):
    assert cid in context.decision.catalyst_ids, (
        f"expected {cid} in {context.decision.catalyst_ids}, missing"
    )
