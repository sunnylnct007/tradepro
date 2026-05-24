"""Steps for dividend_dashboard.feature."""
from __future__ import annotations

import pandas as pd
from behave import given, then, when

from tradepro_strategies.core_portfolio import compute_dividend_dashboard


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


@given('a dividend info dict with')
def step_info(context) -> None:
    info: dict = {}
    for row in context.table:
        info[row["field"]] = _parse_value(row["value"])
    context.dd_info = info
    context.dd_series = None
    context.dd_position_gbp = None


@given('dividend history')
def step_history(context) -> None:
    rows = []
    for row in context.table:
        yr = int(row["year"])
        total = float(row["total"])
        # Pretend a single mid-year payment for simplicity — the
        # _annualise_dividends helper sums by year, so one row per
        # year is enough.
        rows.append((pd.Timestamp(f"{yr}-06-30"), total))
    context.dd_series = pd.Series([t for _, t in rows], index=[d for d, _ in rows])


@given('no dividend history')
def step_no_history(context) -> None:
    context.dd_series = pd.Series([], dtype=float)


@given('a position size of {size:g} GBP')
def step_position(context, size: float) -> None:
    context.dd_position_gbp = float(size)


@when('I compute the dividend dashboard for "{symbol}"')
def step_compute(context, symbol: str) -> None:
    context.dd = compute_dividend_dashboard(
        symbol,
        info=context.dd_info,
        dividends_series=context.dd_series,
        position_size_gbp=getattr(context, "dd_position_gbp", None),
    )


@then('the dividend verdict is "{expected}"')
def step_verdict(context, expected: str) -> None:
    assert context.dd.verdict == expected, (
        f"verdict: expected {expected!r}, got {context.dd.verdict!r} "
        f"(rationale: {context.dd.rationale})"
    )


@then('the dividend yield_pct is approximately {expected:g}')
def step_yield(context, expected: float) -> None:
    assert context.dd.current_yield_pct is not None, "yield is None"
    assert abs(context.dd.current_yield_pct - expected) < 0.1, (
        f"yield_pct: expected ~{expected}, got {context.dd.current_yield_pct}"
    )


@then('the dividend five_year_cagr_pct is greater than {expected:g}')
def step_cagr_gt(context, expected: float) -> None:
    assert context.dd.five_year_cagr_pct is not None, "CAGR is None"
    assert context.dd.five_year_cagr_pct > expected, (
        f"CAGR: expected > {expected}, got {context.dd.five_year_cagr_pct}"
    )


@then('the dividend rationale mentions "{needle}"')
def step_rationale(context, needle: str) -> None:
    assert needle.lower() in (context.dd.rationale or "").lower(), (
        f"rationale {context.dd.rationale!r} does not mention {needle!r}"
    )


@then('the projected_annual_income_gbp is approximately {expected:g}')
def step_projected(context, expected: float) -> None:
    assert context.dd.projected_annual_income_gbp is not None, "projected is None"
    assert abs(context.dd.projected_annual_income_gbp - expected) < 5.0, (
        f"projected: expected ~{expected}, got {context.dd.projected_annual_income_gbp}"
    )
