"""Steps for valuation_layer.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.core_portfolio import compute_valuation_layer


def _parse_value(s: str):
    s = s.strip()
    if s.lower() == "nan":
        return float("nan")
    try:
        if "." in s or s.lstrip("-").isdigit() is False:
            return float(s)
        return int(s)
    except ValueError:
        return s


@given('a valuation info dict with')
def step_info_dict(context) -> None:
    info: dict = {}
    for row in context.table:
        info[row["field"]] = _parse_value(row["value"])
    context.vl_info = info


@given('an empty valuation info dict')
def step_empty_info(context) -> None:
    context.vl_info = {}


@when('I compute the valuation layer for "{symbol}"')
def step_compute(context, symbol: str) -> None:
    context.vl = compute_valuation_layer(symbol, info=context.vl_info)


@then('the overall verdict is "{expected}"')
def step_check_overall(context, expected: str) -> None:
    assert context.vl.overall_verdict == expected, (
        f"overall_verdict: expected {expected!r}, got {context.vl.overall_verdict!r} "
        f"(rationale: {context.vl.rationale})"
    )


@then('the metric "{name}" verdict is "{expected}"')
def step_check_metric_verdict(context, name: str, expected: str) -> None:
    m = next((m for m in context.vl.metrics if m.name == name), None)
    assert m is not None, f"metric {name!r} not found"
    assert m.verdict == expected, (
        f"metric {name!r} verdict: expected {expected!r}, got {m.verdict!r} "
        f"(raw={m.raw})"
    )


@then('the metric "{name}" raw value is missing')
def step_check_metric_missing(context, name: str) -> None:
    m = next((m for m in context.vl.metrics if m.name == name), None)
    assert m is not None, f"metric {name!r} not found"
    assert m.raw is None, f"metric {name!r} raw expected None, got {m.raw}"


@then('the valuation missing_metrics list contains "{name}"')
def step_missing_contains(context, name: str) -> None:
    assert name in context.vl.missing_metrics, (
        f"missing_metrics {context.vl.missing_metrics!r} does not contain {name!r}"
    )


@then('the valuation rationale mentions "{needle}"')
def step_rationale(context, needle: str) -> None:
    assert needle.lower() in (context.vl.rationale or "").lower(), (
        f"rationale {context.vl.rationale!r} does not mention {needle!r}"
    )
