"""Steps for horizon_demotion.feature — pure-function tests of the
new apply_horizon_and_range_demotion rule."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import apply_horizon_and_range_demotion


@given('a starting {bucket} bucket with reason "{reason}"')
def step_initial(context, bucket: str, reason: str) -> None:
    context.bucket = bucket
    context.reason = reason
    context.horizon = None
    context.range_pct = None


@given('horizon_classification swing signal is "{signal}" with score {score:d}')
def step_horizon(context, signal: str, score: int) -> None:
    context.horizon = {"swing": {"signal": signal, "score": score}}


@given("range_pct is {value:g}")
def step_range(context, value: float) -> None:
    context.range_pct = value


@when("I apply the horizon and range demotion")
def step_apply(context) -> None:
    out_bucket, out_reason, out_demoted = apply_horizon_and_range_demotion(
        bucket=context.bucket,
        reason=context.reason,
        horizon_classification=context.horizon,
        range_pct=context.range_pct,
    )
    context.out_bucket = out_bucket
    context.out_reason = out_reason
    context.out_demoted = out_demoted


@then('the resulting bucket is "{expected}"')
def step_resulting_bucket(context, expected: str) -> None:
    assert context.out_bucket == expected, (
        f"expected bucket {expected!r}, got {context.out_bucket!r}"
    )


@then("the horizon demoted flag is {expected}")
def step_horizon_demoted(context, expected: str) -> None:
    expected_bool = {"True": True, "False": False}[expected]
    assert context.out_demoted is expected_bool, (
        f"expected demoted={expected_bool}, got {context.out_demoted}"
    )


@then('the horizon demotion reason mentions "{snippet}"')
def step_horizon_reason_mentions(context, snippet: str) -> None:
    assert snippet in context.out_reason, (
        f"expected reason to mention {snippet!r}, got {context.out_reason!r}"
    )
