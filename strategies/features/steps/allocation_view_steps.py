"""Steps for allocation_view.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.core_portfolio import (
    CoreSleevePosition,
    compute_allocation_view,
)


def _opt_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    return float(s)


@given('core sleeve positions')
def step_positions(context) -> None:
    positions: list[CoreSleevePosition] = []
    for row in context.table:
        if not (row["symbol"] or "").strip():
            continue
        positions.append(CoreSleevePosition(
            symbol=row["symbol"],
            quantity=float(row["quantity"]),
            cost_basis_gbp=float(row["cost_basis_gbp"]),
            current_price_gbp=float(row["current_price_gbp"]),
            yield_pct=_opt_float(row["yield_pct"]),
            planned_monthly_gbp=float(row["planned_monthly_gbp"] or 0),
        ))
    context.av_positions = positions


@when('I compute the allocation view with portfolio {total:g}')
def step_compute_with_total(context, total: float) -> None:
    context.av = compute_allocation_view(
        context.av_positions,
        total_portfolio_value_gbp=total,
    )


@when('I compute the allocation view without a portfolio value')
def step_compute_no_total(context) -> None:
    context.av = compute_allocation_view(context.av_positions)


@then('the sleeve_market_value_gbp is approximately {expected:g}')
def step_mv(context, expected: float) -> None:
    assert abs(context.av.sleeve_market_value_gbp - expected) < 1.0, (
        f"sleeve_market_value_gbp: expected ~{expected}, got "
        f"{context.av.sleeve_market_value_gbp}"
    )


@then('the sleeve_cost_basis_gbp is approximately {expected:g}')
def step_cb(context, expected: float) -> None:
    assert abs(context.av.sleeve_cost_basis_gbp - expected) < 1.0, (
        f"cost_basis_gbp: expected ~{expected}, got "
        f"{context.av.sleeve_cost_basis_gbp}"
    )


@then('the sleeve_unrealised_gain_gbp is approximately {expected:g}')
def step_gain(context, expected: float) -> None:
    assert abs(context.av.sleeve_unrealised_gain_gbp - expected) < 1.0, (
        f"unrealised_gain_gbp: expected ~{expected}, got "
        f"{context.av.sleeve_unrealised_gain_gbp}"
    )


@then('the planned_monthly_inflow_gbp is approximately {expected:g}')
def step_inflow(context, expected: float) -> None:
    assert abs(context.av.planned_monthly_inflow_gbp - expected) < 1.0, (
        f"planned_monthly_inflow_gbp: expected ~{expected}, got "
        f"{context.av.planned_monthly_inflow_gbp}"
    )


@then('the weighted_yield_pct is approximately {expected:g}')
def step_weighted_yield(context, expected: float) -> None:
    actual = context.av.weighted_yield_pct
    assert actual is not None, "weighted_yield_pct is None"
    assert abs(actual - expected) < 0.5, (
        f"weighted_yield_pct: expected ~{expected}, got {actual}"
    )


@then('the sleeve status is "{expected}"')
def step_status(context, expected: str) -> None:
    assert context.av.status == expected, (
        f"sleeve status: expected {expected!r}, got {context.av.status!r}"
    )


@then('the sleeve_pct_of_portfolio is less than {expected:g}')
def step_sleeve_pct_lt(context, expected: float) -> None:
    actual = context.av.sleeve_pct_of_portfolio
    assert actual is not None, "sleeve_pct_of_portfolio is None"
    assert actual < expected, (
        f"sleeve_pct_of_portfolio: expected < {expected}, got {actual}"
    )


@then('the sleeve_pct_of_portfolio is greater than {expected:g}')
def step_sleeve_pct_gt(context, expected: float) -> None:
    actual = context.av.sleeve_pct_of_portfolio
    assert actual is not None, "sleeve_pct_of_portfolio is None"
    assert actual > expected, (
        f"sleeve_pct_of_portfolio: expected > {expected}, got {actual}"
    )


@then('the sleeve_pct_of_portfolio is null')
def step_sleeve_pct_null(context) -> None:
    assert context.av.sleeve_pct_of_portfolio is None, (
        f"sleeve_pct_of_portfolio: expected None, got {context.av.sleeve_pct_of_portfolio}"
    )


@then('the sleeve has {n:d} position breakdowns')
def step_position_count(context, n: int) -> None:
    assert len(context.av.positions) == n, (
        f"position count: expected {n}, got {len(context.av.positions)}"
    )


@then('the breakdown for "{symbol}" has weight approximately {expected:g}')
def step_breakdown_weight(context, symbol: str, expected: float) -> None:
    p = next((p for p in context.av.positions if p.symbol == symbol.upper()), None)
    assert p is not None, f"breakdown for {symbol!r} not found"
    assert abs(p.weight_pct - expected) < 1.0, (
        f"weight for {symbol!r}: expected ~{expected}, got {p.weight_pct}"
    )


@then('the breakdown for "{symbol}" has projected_annual_income_gbp approximately {expected:g}')
def step_breakdown_income(context, symbol: str, expected: float) -> None:
    p = next((p for p in context.av.positions if p.symbol == symbol.upper()), None)
    assert p is not None, f"breakdown for {symbol!r} not found"
    assert p.projected_annual_income_gbp is not None, (
        f"projected income for {symbol!r} is None"
    )
    assert abs(p.projected_annual_income_gbp - expected) < 5.0, (
        f"income for {symbol!r}: expected ~{expected}, got "
        f"{p.projected_annual_income_gbp}"
    )


@then('the breakdown for "{symbol}" has projected_annual_income_gbp null')
def step_breakdown_income_null(context, symbol: str) -> None:
    p = next((p for p in context.av.positions if p.symbol == symbol.upper()), None)
    assert p is not None, f"breakdown for {symbol!r} not found"
    assert p.projected_annual_income_gbp is None, (
        f"projected income for {symbol!r}: expected None, got "
        f"{p.projected_annual_income_gbp}"
    )
