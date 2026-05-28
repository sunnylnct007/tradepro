"""Step definitions for schema.feature."""
from __future__ import annotations

from behave import given, when, then

from tradepro_strategies.schema import (
    SCHEMA_VERSION,
    CompareLlmDemotionRule,
    ComparePayload,
)


@given("a minimal compare payload dict")
def step_minimal_payload(context) -> None:
    context.payload_dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "compare",
        "generated_at": "2026-05-02T00:00:00Z",
        "from": "2010-01-01",
        "to": "2026-05-02",
        "provider": "yahoo",
        "currency": "USD",
        "rank_metric": "sharpe",
        "rows": [],
    }


@given('a payload missing the "rows" field with rows replaced by a string')
def step_bad_rows(context) -> None:
    context.payload_dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "compare",
        "generated_at": "2026-05-02T00:00:00Z",
        "from": "2010-01-01",
        "to": "2026-05-02",
        "provider": "yahoo",
        "currency": "USD",
        "rank_metric": "sharpe",
        "rows": "this should be a list, not a string",
    }


@when("I validate it via ComparePayload")
def step_validate(context) -> None:
    context.validation_error = None
    try:
        context.parsed = ComparePayload.from_payload_dict(context.payload_dict)
    except Exception as e:  # noqa: BLE001
        context.parsed = None
        context.validation_error = str(e)


@then("validation succeeds")
def step_validation_succeeds(context) -> None:
    assert context.validation_error is None, f"got error: {context.validation_error}"
    assert context.parsed is not None


@then("the schema_version is the current SCHEMA_VERSION")
def step_schema_version(context) -> None:
    assert context.parsed.schema_version == SCHEMA_VERSION


@then("validation fails with a list-type error")
def step_validation_fails(context) -> None:
    assert context.validation_error is not None
    assert "list" in context.validation_error.lower(), context.validation_error


@given("a CompareLlmDemotionRule with threshold {threshold:f} and min_material {n:d}")
def step_demotion_rule(context, threshold: float, n: int) -> None:
    context.rule = CompareLlmDemotionRule(
        mean_sentiment_threshold=threshold,
        min_material_negative_count=n,
        lookback_days=7,
        description="test rule",
    )


@when("I serialise and re-validate")
def step_serialise_revalidate(context) -> None:
    raw = context.rule.model_dump()
    context.parsed = CompareLlmDemotionRule.model_validate(raw)


@then("the values are preserved exactly")
def step_values_preserved(context) -> None:
    assert context.parsed.mean_sentiment_threshold == context.rule.mean_sentiment_threshold
    assert context.parsed.min_material_negative_count == context.rule.min_material_negative_count
