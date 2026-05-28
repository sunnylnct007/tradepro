"""Steps for sentiment_demotion.feature — pin Tier 1 (any → AVOID at
-0.45) and Tier 2 (BUY → WAIT at -0.30) independently of the wider
compare flow."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import apply_sentiment_demotion


@given("a {bucket} with sentiment mean {mean:g} and {n:d} material-negative headlines")
def step_input(context, bucket: str, mean: float, n: int):
    context.bucket_in = bucket
    context.mean_in = mean
    context.mat_neg_in = n


@given("a {bucket} with no sentiment data")
def step_input_no_data(context, bucket: str):
    context.bucket_in = bucket
    context.mean_in = None
    context.mat_neg_in = None


@when("I apply sentiment demotion")
def step_apply(context):
    bucket, reason, demoted = apply_sentiment_demotion(
        bucket=context.bucket_in,
        reason=f"original {context.bucket_in} reason",
        mean=context.mean_in,
        material_negative_count=context.mat_neg_in,
    )
    context.bucket_out = bucket
    # Reuse `context.reason` so bucket_steps.py's "the reason mentions"
    # step finds the demotion-derived reason without redefinition.
    context.reason = reason
    context.demoted_out = demoted


@then('the bucket becomes "{expected}"')
def step_bucket_eq(context, expected: str):
    assert context.bucket_out == expected, (
        f"expected {expected!r}, got {context.bucket_out!r} "
        f"(reason={context.reason_out!r}, demoted={context.demoted_out})"
    )


@then("the demoted flag is set")
def step_demoted(context):
    assert context.demoted_out is True, "expected demoted=True"


@then("the demoted flag is not set")
def step_not_demoted(context):
    assert context.demoted_out is False, (
        f"expected demoted=False (reason={context.reason_out!r})"
    )


# `the reason mentions "..."` is shared with bucket_steps.py — behave
# registers steps once; reusing the bucket implementation works as
# long as we drop our reason into the same context attribute.
# bucket_steps.py:45 reads context.reason — alias here so the step
# matcher finds it.
