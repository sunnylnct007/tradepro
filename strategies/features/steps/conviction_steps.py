"""Steps for conviction.feature — direct unit tests of
`compute_conviction` and `cap_bucket_at_low_conviction`."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import (
    cap_bucket_at_low_conviction,
    compute_conviction,
)


_ICHI_MAP = {
    "above-cloud": "ABOVE_CLOUD",
    "in-cloud": "IN_CLOUD",
    "below-cloud": "BELOW_CLOUD",
}


def _build_state(above_sma: bool, ichi: str, volume_ratio: float | None) -> dict:
    return {
        "above_sma_200": above_sma,
        "ichimoku_cloud_position": _ICHI_MAP[ichi],
        "volume_ratio_20d": volume_ratio,
    }


@given('a market_state with above_sma_200 {above_sma:S} and ichimoku {ichi:S}')
def step_state_no_vol(context, above_sma: str, ichi: str) -> None:
    context.market_state = _build_state(above_sma == "true", ichi, None)
    context.sentiment_demoted = False
    context.horizon_demoted = False


@given('a market_state with above_sma_200 {above_sma:S} and ichimoku {ichi:S} '
       'and volume_ratio_20d {vol:f}')
def step_state_with_vol(context, above_sma: str, ichi: str, vol: float) -> None:
    context.market_state = _build_state(above_sma == "true", ichi, vol)
    context.sentiment_demoted = False
    context.horizon_demoted = False


@when('I compute conviction with bucket "{bucket}"')
def step_compute_conviction(context, bucket: str) -> None:
    context.conviction, context.conviction_reason = compute_conviction(
        bucket=bucket,
        market_state=context.market_state,
        sentiment_demoted=getattr(context, "sentiment_demoted", False),
        horizon_demoted=getattr(context, "horizon_demoted", False),
    )


@when('I compute conviction with bucket "{bucket}" and sentiment_demoted true')
def step_compute_conviction_sentiment(context, bucket: str) -> None:
    context.conviction, context.conviction_reason = compute_conviction(
        bucket=bucket,
        market_state=context.market_state,
        sentiment_demoted=True,
        horizon_demoted=False,
    )


@when('I compute conviction with bucket "{bucket}" and horizon_demoted true')
def step_compute_conviction_horizon(context, bucket: str) -> None:
    context.conviction, context.conviction_reason = compute_conviction(
        bucket=bucket,
        market_state=context.market_state,
        sentiment_demoted=False,
        horizon_demoted=True,
    )


@then('the conviction is "{expected}"')
def step_check_conviction(context, expected: str) -> None:
    assert context.conviction == expected, (
        f"conviction: expected {expected!r}, got {context.conviction!r}"
    )


@then('the conviction reason mentions "{needle}"')
def step_check_conviction_reason(context, needle: str) -> None:
    assert needle.lower() in context.conviction_reason.lower(), (
        f"conviction reason {context.conviction_reason!r} does not mention "
        f"{needle!r}"
    )


@given('a bucket "{bucket}" with reason "{reason}" and conviction "{conviction}"')
def step_seed_bucket(context, bucket: str, reason: str, conviction: str) -> None:
    context.bucket_in = bucket
    context.reason_in = reason
    context.conviction_in = conviction


@when('I cap the bucket at low conviction')
def step_cap_bucket(context) -> None:
    context.bucket_out, context.reason_out, context.conviction_demoted = (
        cap_bucket_at_low_conviction(
            bucket=context.bucket_in,
            reason=context.reason_in,
            conviction=context.conviction_in,
        )
    )


@then('the capped bucket is "{expected}"')
def step_check_bucket(context, expected: str) -> None:
    assert context.bucket_out == expected, (
        f"capped bucket: expected {expected!r}, got {context.bucket_out!r}"
    )


@then('the conviction-demoted flag is {expected:S}')
def step_check_conviction_demoted(context, expected: str) -> None:
    want = expected == "True"
    assert context.conviction_demoted == want, (
        f"conviction_demoted: expected {want}, got {context.conviction_demoted}"
    )


@then('the capped reason mentions "{needle}"')
def step_check_reason_out(context, needle: str) -> None:
    assert needle.lower() in (context.reason_out or "").lower(), (
        f"capped reason {context.reason_out!r} does not mention {needle!r}"
    )
