"""Steps for coherence_enforcement.feature — direct unit test of
`enforce_coherence` against synthetic rows."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.compare import enforce_coherence
from tradepro_strategies.regression_panel import ASSERTION_RESOLVERS


@given('a compare row with bucket "{bucket}" and raw entry_signal "{entry}"')
def step_row_with_inputs(context, bucket: str, entry: str) -> None:
    context.bucket = bucket
    context.row = {
        "symbol": "TEST",
        "market_state": {"entry_signal": entry},
    }


@when('I enforce coherence on the row')
def step_enforce_default(context) -> None:
    enforce_coherence(
        context.row,
        bucket=context.bucket,
        sentiment_demoted=False,
        horizon_demoted=False,
    )


@when('I enforce coherence on the row with sentiment_demoted true')
def step_enforce_sentiment(context) -> None:
    enforce_coherence(
        context.row,
        bucket=context.bucket,
        sentiment_demoted=True,
        horizon_demoted=False,
    )


@when('I enforce coherence on the row with horizon_demoted true')
def step_enforce_horizon(context) -> None:
    enforce_coherence(
        context.row,
        bucket=context.bucket,
        sentiment_demoted=False,
        horizon_demoted=True,
    )


@then('the row\'s market_state.entry_signal is "{expected}"')
def step_check_entry_signal(context, expected: str) -> None:
    actual = (context.row["market_state"] or {}).get("entry_signal")
    assert actual == expected, f"entry_signal: expected {expected!r}, got {actual!r}"


@then('the row\'s market_state.raw_entry_signal is "{expected}"')
def step_check_raw_entry_signal(context, expected: str) -> None:
    actual = (context.row["market_state"] or {}).get("raw_entry_signal")
    assert actual == expected, (
        f"raw_entry_signal: expected {expected!r}, got {actual!r}"
    )


@then('the row has no market_state.raw_entry_signal')
def step_check_no_raw_entry_signal(context) -> None:
    actual = (context.row["market_state"] or {}).get("raw_entry_signal")
    assert actual is None, (
        f"raw_entry_signal should be absent, got {actual!r}"
    )


@then('the row\'s coherence.today_bucket is "{expected}"')
def step_check_coherence_bucket(context, expected: str) -> None:
    actual = (context.row.get("coherence") or {}).get("today_bucket")
    assert actual == expected, (
        f"coherence.today_bucket: expected {expected!r}, got {actual!r}"
    )


@then('the row\'s coherence.entry_signal is "{expected}"')
def step_check_coherence_entry(context, expected: str) -> None:
    actual = (context.row.get("coherence") or {}).get("entry_signal")
    assert actual == expected, (
        f"coherence.entry_signal: expected {expected!r}, got {actual!r}"
    )


@then('the row\'s coherence.consistent flag is true')
def step_check_consistent_true(context) -> None:
    actual = (context.row.get("coherence") or {}).get("consistent")
    assert actual is True, f"coherence.consistent: expected True, got {actual!r}"


@then('the row\'s coherence.supersede_reason is null')
def step_check_supersede_null(context) -> None:
    actual = (context.row.get("coherence") or {}).get("supersede_reason")
    assert actual is None, (
        f"coherence.supersede_reason: expected None, got {actual!r}"
    )


@then('the row\'s coherence.supersede_reason is "{expected}"')
def step_check_supersede(context, expected: str) -> None:
    actual = (context.row.get("coherence") or {}).get("supersede_reason")
    assert actual == expected, (
        f"coherence.supersede_reason: expected {expected!r}, got {actual!r}"
    )


@then('the regression panel coherence_check resolver returns "{expected}" for expected "{exp}"')
def step_check_panel_resolver(context, expected: str, exp: str) -> None:
    # The production comparator sets row['bucket'] alongside the
    # coherence enforcement; mirror that so the resolver can read it.
    context.row["bucket"] = context.bucket
    resolver = ASSERTION_RESOLVERS["coherence_check"]
    status, actual, _expected_in, detail = resolver(exp, context.row)
    assert status == expected, (
        f"resolver status: expected {expected!r}, got {status!r}\n"
        f"  actual={actual!r} detail={detail}"
    )
