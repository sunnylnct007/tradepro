"""Steps for regression_panel.feature — synthetic-row sanity test of
the assertion engine that backs tradepro-regression-panel."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.regression_panel import evaluate_case


@given('a regression case asserting bucket "{value}"')
def step_case_bucket(context, value: str) -> None:
    context.case = {
        "id": "TEST-001",
        "ticker": "TEST",
        "category": "synthetic",
        "expected": {"swing_bucket": value},
    }


@given('a regression case asserting coherence_check "{value}"')
def step_case_coherence(context, value: str) -> None:
    context.case = {
        "id": "TEST-002",
        "ticker": "TEST",
        "category": "synthetic",
        "expected": {"coherence_check": value},
    }


@given('a regression case asserting {key} "{value}"')
def step_case_arbitrary(context, key: str, value: str) -> None:
    context.case = {
        "id": "TEST-999",
        "ticker": "TEST",
        "category": "synthetic",
        "expected": {key: value},
    }


@given('the compare row has bucket "{bucket}" with entry_signal "{entry}"')
def step_row_with_entry(context, bucket: str, entry: str) -> None:
    context.row = {
        "bucket": bucket,
        "market_state": {"entry_signal": entry},
    }


@given('the compare row has bucket "{bucket}"')
def step_row_bucket(context, bucket: str) -> None:
    context.row = {"bucket": bucket}


@given("no compare row is available")
def step_no_row(context) -> None:
    context.row = None


@when("I evaluate the case")
def step_evaluate(context) -> None:
    context.result = evaluate_case(context.case, getattr(context, "row", None))


@then('the case status is "{expected}"')
def step_status(context, expected: str) -> None:
    actual = context.result.status
    assert actual == expected, (
        f"case status: expected {expected!r}, got {actual!r}\n"
        f"  assertions: {context.result.assertions!r}\n"
        f"  error: {context.result.error!r}"
    )
