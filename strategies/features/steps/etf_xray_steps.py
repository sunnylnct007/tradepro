"""Steps for etf_xray.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.core_portfolio import (
    compute_etf_xray,
    compute_overlap,
    project_drip_value,
)


def _opt_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    return float(s)


def _holdings_from_table(table) -> list[dict]:
    out: list[dict] = []
    for row in table:
        out.append({
            "symbol":     row["symbol"],
            "name":       row["name"],
            "weight_pct": _opt_float(row["weight_pct"]),
        })
    return out


@given('an ETF "{symbol}" with holdings')
def step_xray_etf(context, symbol: str) -> None:
    context.xray_symbol = symbol
    context.xray_holdings = _holdings_from_table(context.table)
    context.xray_expense = None
    context.xray_yield = None


@given('expense_ratio_pct {expense:g}')
def step_xray_expense(context, expense: float) -> None:
    context.xray_expense = float(expense)


@given('current_yield_pct {y:g}')
def step_xray_yield(context, y: float) -> None:
    context.xray_yield = float(y)


@when('I compute the ETF xray')
def step_compute_xray(context) -> None:
    context.xray = compute_etf_xray(
        context.xray_symbol,
        top_holdings=context.xray_holdings,
        expense_ratio_pct=context.xray_expense,
        current_yield_pct=context.xray_yield,
    )


@then('the xray holding_count is {n:d}')
def step_holding_count(context, n: int) -> None:
    assert context.xray.holding_count == n, (
        f"holding_count: expected {n}, got {context.xray.holding_count}"
    )


@then('the xray expense_ratio_pct is approximately {expected:g}')
def step_xray_expense_check(context, expected: float) -> None:
    actual = context.xray.expense_ratio_pct
    assert actual is not None, "expense_ratio_pct is None"
    assert abs(actual - expected) < 0.01, (
        f"expense_ratio_pct: expected ~{expected}, got {actual}"
    )


@then('the holding "{symbol}" weight is approximately {expected:g}')
def step_holding_weight(context, symbol: str, expected: float) -> None:
    sym = symbol.upper()
    match = next((h for h in context.xray.top_holdings if h["symbol"] == sym), None)
    assert match is not None, f"holding {sym!r} not found"
    actual = match["weight_pct"]
    assert abs(actual - expected) < 0.1, (
        f"weight for {sym!r}: expected ~{expected}, got {actual}"
    )


# ─────────── overlap ───────────


@given('ETF "{symbol}" with holdings')
def step_overlap_etf(context, symbol: str) -> None:
    if not hasattr(context, "overlap_etfs"):
        context.overlap_etfs = {}
    context.overlap_etfs[symbol.upper()] = _holdings_from_table(context.table)


@when('I compute overlap between "{a}" and "{b}"')
def step_compute_overlap(context, a: str, b: str) -> None:
    context.overlap = compute_overlap(
        a, context.overlap_etfs[a.upper()],
        b, context.overlap_etfs[b.upper()],
    )


@then('the overlap_pct is approximately {expected:g}')
def step_overlap_pct(context, expected: float) -> None:
    actual = context.overlap.overlap_pct
    assert abs(actual - expected) < 0.5, (
        f"overlap_pct: expected ~{expected}, got {actual}"
    )


@then('the overlap_pct is greater than {expected:g}')
def step_overlap_pct_gt(context, expected: float) -> None:
    actual = context.overlap.overlap_pct
    assert actual > expected, (
        f"overlap_pct: expected > {expected}, got {actual}"
    )


@then('the shared_count is {n:d}')
def step_shared_count(context, n: int) -> None:
    assert context.overlap.shared_count == n, (
        f"shared_count: expected {n}, got {context.overlap.shared_count}"
    )


@then('the overlap rationale mentions "{needle}"')
def step_overlap_rationale(context, needle: str) -> None:
    actual = context.overlap.rationale or ""
    assert needle.lower() in actual.lower(), (
        f"rationale {actual!r} does not mention {needle!r}"
    )


@then('the first contribution symbol is "{expected}"')
def step_first_contrib(context, expected: str) -> None:
    assert context.overlap.contributions, "no contributions present"
    first = context.overlap.contributions[0].symbol
    assert first == expected.upper(), (
        f"first contribution symbol: expected {expected!r}, got {first!r}"
    )


# ─────────── DRIP projection ───────────


@when('I project DRIP from {value:g} GBP at {yield_pct:g}% yield for {years:d} years with {growth:g} percent price change')
def step_project_drip(context, value: float, yield_pct: float, years: int, growth: float) -> None:
    context.drip = project_drip_value(
        current_value_gbp=value,
        yield_pct=yield_pct,
        years=years,
        annual_price_change_pct=growth,
    )


@then('the projected end_value_gbp is approximately {expected:g}')
def step_drip_end(context, expected: float) -> None:
    actual = context.drip["end_value_gbp"]
    assert actual is not None, "end_value_gbp is None"
    assert abs(actual - expected) < expected * 0.01, (
        f"end_value_gbp: expected ~{expected}, got {actual}"
    )


@then('the projected end_value_gbp is greater than {expected:g}')
def step_drip_end_gt(context, expected: float) -> None:
    actual = context.drip["end_value_gbp"]
    assert actual > expected, (
        f"end_value_gbp: expected > {expected}, got {actual}"
    )


@then('the dividends_reinvested_gbp is approximately {expected:g}')
def step_drip_divs(context, expected: float) -> None:
    actual = context.drip["total_dividends_reinvested_gbp"]
    assert actual is not None, "dividends_reinvested_gbp is None"
    tol = max(abs(expected) * 0.01, 1.0)
    assert abs(actual - expected) < tol, (
        f"dividends_reinvested_gbp: expected ~{expected}, got {actual}"
    )
