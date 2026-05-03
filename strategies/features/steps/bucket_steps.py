"""Steps for bucket.feature — pure-function tests of compute_bucket."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import compute_bucket


@given('price verdict {verdict} with reason "{reason}"')
def step_price_verdict(context, verdict: str, reason: str):
    context.price_verdict = verdict
    context.price_reason = reason


@given("price verdict {verdict} with no reason")
def step_price_verdict_none(context, verdict: str):
    context.price_verdict = verdict
    context.price_reason = None


@given("{long_count:d} of {total:d} strategies currently long")
def step_long_count(context, long_count: int, total: int):
    context.long_count = long_count
    context.total = total


@when("I compute the bucket")
def step_compute(context):
    context.bucket, context.reason = compute_bucket(
        price_verdict=context.price_verdict,
        price_reason=context.price_reason,
        long_count=context.long_count,
        total=context.total,
    )


@then("the bucket is {expected}")
def step_assert_bucket(context, expected: str):
    assert context.bucket == expected, (
        f"expected bucket={expected!r}, got {context.bucket!r} "
        f"(reason: {context.reason!r})"
    )


@then('the reason mentions "{snippet}"')
def step_assert_reason(context, snippet: str):
    assert snippet in context.reason, (
        f"reason {context.reason!r} does not contain {snippet!r}"
    )
