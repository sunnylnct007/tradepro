"""Steps for earnings_suppressor.feature — pins apply_earnings_suppressor.
Phrasing prefixed with "post-suppressor" to avoid collisions with the
shared @then('the conviction is …') etc steps in conviction_steps.py
and horizon_demotion_steps.py."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import apply_earnings_suppressor


@given('a pre-earnings bucket "{bucket}" with reason "{reason}"')
def step_bucket_reason(context, bucket: str, reason: str) -> None:
    context.es_bucket = bucket
    context.es_reason = reason


@given('pre-earnings conviction "{conviction}"')
def step_conviction(context, conviction: str) -> None:
    context.es_conviction = conviction


@given('earnings in {days:d} days')
def step_earnings_days(context, days: int) -> None:
    context.es_days = days


@given('earnings days_until is None')
def step_earnings_none(context) -> None:
    context.es_days = None


@when('I apply the earnings suppressor')
def step_apply(context) -> None:
    (context.es_bucket_out, context.es_reason_out,
     context.es_conviction_out, context.es_flag) = apply_earnings_suppressor(
        bucket=context.es_bucket,
        reason=context.es_reason,
        conviction=context.es_conviction,
        days_until_earnings=context.es_days,
    )


@then('the post-suppressor bucket is "{expected}"')
def step_check_bucket(context, expected: str) -> None:
    assert context.es_bucket_out == expected, (
        f"bucket: expected {expected!r}, got {context.es_bucket_out!r}"
    )


@then('the post-suppressor conviction is "{expected}"')
def step_check_conviction(context, expected: str) -> None:
    assert context.es_conviction_out == expected, (
        f"conviction: expected {expected!r}, got {context.es_conviction_out!r}"
    )


@then('the suppressed flag is {flag}')
def step_check_flag(context, flag: str) -> None:
    want = flag == "True"
    assert context.es_flag == want, (
        f"suppressed flag: expected {want}, got {context.es_flag}"
    )


@then('the post-suppressor reason mentions "{needle}"')
def step_check_reason(context, needle: str) -> None:
    assert needle.lower() in (context.es_reason_out or "").lower(), (
        f"reason {context.es_reason_out!r} does not mention {needle!r}"
    )
