"""Steps for quality_scorecard.feature."""
from __future__ import annotations

import math

from behave import given, then, when

from tradepro_strategies.core_portfolio import compute_quality_scorecard


def _parse_value(s: str):
    s = s.strip()
    if s.lower() == "nan":
        return float("nan")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        return s


@given('a fundamentals info dict with')
def step_info_dict(context) -> None:
    info: dict = {}
    for row in context.table:
        info[row["field"]] = _parse_value(row["value"])
    context.qs_info = info


@given('an empty fundamentals info dict')
def step_empty_info(context) -> None:
    context.qs_info = {}


@when('I compute the quality scorecard for "{symbol}"')
def step_compute(context, symbol: str) -> None:
    context.qs = compute_quality_scorecard(symbol, info=context.qs_info)


@then('the scorecard stars is {n:d}')
def step_check_stars(context, n: int) -> None:
    assert context.qs.stars == n, f"stars: expected {n}, got {context.qs.stars}"


@then('the scorecard stars is at least {n:d}')
def step_check_stars_at_least(context, n: int) -> None:
    assert context.qs.stars >= n, (
        f"stars: expected ≥ {n}, got {context.qs.stars}"
    )


@then('the scorecard stars is less than {n:d}')
def step_check_stars_less_than(context, n: int) -> None:
    assert context.qs.stars < n, (
        f"stars: expected < {n}, got {context.qs.stars}"
    )


@then('the overall_score is {expected:g}')
def step_check_overall(context, expected: float) -> None:
    assert abs(context.qs.overall_score - expected) < 0.01, (
        f"overall_score: expected {expected}, got {context.qs.overall_score}"
    )


@then('the metric "{name}" has score {score:d}')
def step_check_metric_score(context, name: str, score: int) -> None:
    m = next((m for m in context.qs.metrics if m.name == name), None)
    assert m is not None, f"metric {name!r} not found in scorecard"
    assert m.score == score, (
        f"metric {name!r} score: expected {score}, got {m.score} (raw={m.raw!r})"
    )


@then('the metric "{name}" raw value is approximately {expected:g}')
def step_check_metric_raw(context, name: str, expected: float) -> None:
    m = next((m for m in context.qs.metrics if m.name == name), None)
    assert m is not None, f"metric {name!r} not found"
    assert m.raw is not None, f"metric {name!r} raw is None"
    assert abs(m.raw - expected) < 0.01, (
        f"metric {name!r} raw: expected ~{expected}, got {m.raw}"
    )


@then('the missing_metrics list is empty')
def step_missing_empty(context) -> None:
    assert context.qs.missing_metrics == [], (
        f"missing_metrics: expected [], got {context.qs.missing_metrics!r}"
    )


@then('the missing_metrics list contains "{name}"')
def step_missing_contains(context, name: str) -> None:
    assert name in context.qs.missing_metrics, (
        f"missing_metrics {context.qs.missing_metrics!r} does not contain {name!r}"
    )


@then('the to_dict payload has stars_display matching "{minimum}" or stronger')
def step_check_stars_display(context, minimum: str) -> None:
    payload = context.qs.to_dict()
    display = payload.get("stars_display") or ""
    actual_stars = display.count("★")
    min_stars = minimum.count("★")
    assert actual_stars >= min_stars, (
        f"stars_display {display!r} has {actual_stars} stars, expected ≥ {min_stars}"
    )
