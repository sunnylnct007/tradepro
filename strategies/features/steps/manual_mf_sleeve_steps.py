"""Steps for manual_mf_sleeve.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.core_portfolio import (
    ManualMFHolding,
    compute_mf_sleeve,
)


def _opt_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    return float(s)


def _ensure(context) -> None:
    if not hasattr(context, "mf_holdings"):
        context.mf_holdings = []
    if not hasattr(context, "mf_fx"):
        context.mf_fx = {}
    if not hasattr(context, "mf_total_portfolio"):
        context.mf_total_portfolio = None
    if not hasattr(context, "mf_target"):
        context.mf_target = 25.0


@given('an MF sleeve with no holdings')
def step_no_holdings(context) -> None:
    _ensure(context)
    context.mf_holdings = []


@given('an MF FX rate {cur} -> GBP {rate:g}')
def step_fx(context, cur: str, rate: float) -> None:
    _ensure(context)
    context.mf_fx[cur.upper()] = float(rate)


@given('an MF holding')
def step_add_holding(context) -> None:
    _ensure(context)
    # Table is always single-row in this feature (one holding per
    # Given block). Read the first data row.
    row = list(context.table)[0]

    def _get(key: str, default=None):
        try:
            return row[key]
        except KeyError:
            return default

    context.mf_holdings.append(ManualMFHolding(
        fund_name=row["fund_name"],
        units=float(row["units"]),
        last_nav=float(row["last_nav"]),
        last_nav_date=row["last_nav_date"],
        currency=row["currency"],
        cost_basis_local=float(row["cost_basis_local"]),
        fund_type=(_get("fund_type") or "equity"),
        region=(_get("region") or None),
        isin=(_get("isin") or None),
        distribution_yield_pct=_opt_float(_get("distribution_yield_pct", "")),
        monthly_sip_local=float(_get("monthly_sip_local", "0") or 0.0),
    ))


@when('I compute the MF sleeve as of "{today}"')
def step_compute(context, today: str) -> None:
    _ensure(context)
    context.mf_sleeve = compute_mf_sleeve(
        context.mf_holdings,
        fx_to_gbp=context.mf_fx,
        today=today,
    )


@when('I compute the MF sleeve as of "{today}" with total portfolio {total:g} and target {target:g}')
def step_compute_with_target(context, today: str, total: float, target: float) -> None:
    _ensure(context)
    context.mf_sleeve = compute_mf_sleeve(
        context.mf_holdings,
        fx_to_gbp=context.mf_fx,
        today=today,
        total_portfolio_value_gbp=float(total),
        target_sleeve_pct=float(target),
    )


def _approx(actual: float, expected: float, tol: float | None = None) -> bool:
    tol = tol if tol is not None else max(abs(expected) * 0.01, 0.5)
    return abs(actual - expected) <= tol


@then('the MF sleeve market value is approximately {expected:g}')
def step_mv(context, expected: float) -> None:
    actual = context.mf_sleeve.sleeve_market_value_gbp
    assert _approx(actual, expected), (
        f"sleeve_market_value_gbp: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve unrealised gain is approximately {expected:g}')
def step_gain(context, expected: float) -> None:
    actual = context.mf_sleeve.sleeve_unrealised_gain_gbp
    assert _approx(actual, expected), (
        f"sleeve_unrealised_gain_gbp: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve unrealised gain pct is approximately {expected:g}')
def step_gain_pct(context, expected: float) -> None:
    actual = context.mf_sleeve.sleeve_unrealised_gain_pct
    assert _approx(actual, expected, tol=0.5), (
        f"sleeve_unrealised_gain_pct: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve nav_freshness is "{expected}"')
def step_freshness(context, expected: str) -> None:
    actual = context.mf_sleeve.nav_freshness
    assert actual == expected, f"nav_freshness: expected {expected!r}, got {actual!r}"


@then('the MF sleeve status is "{expected}"')
def step_status(context, expected: str) -> None:
    actual = context.mf_sleeve.status
    assert actual == expected, f"status: expected {expected!r}, got {actual!r}"


@then('the MF sleeve has {n:d} holdings')
def step_count(context, n: int) -> None:
    actual = len(context.mf_sleeve.holdings)
    assert actual == n, f"holdings count: expected {n}, got {actual}"


@then('the MF sleeve stale_count is {n:d}')
def step_stale(context, n: int) -> None:
    actual = context.mf_sleeve.stale_count
    assert actual == n, f"stale_count: expected {n}, got {actual}"


@then('the MF holding "{name}" nav_status is "{expected}"')
def step_holding_status(context, name: str, expected: str) -> None:
    h = next((h for h in context.mf_sleeve.holdings if h.fund_name == name), None)
    assert h is not None, f"holding {name!r} not found"
    assert h.nav_status == expected, (
        f"{name} nav_status: expected {expected!r}, got {h.nav_status!r}"
    )


@then('the MF holding "{name}" market_value_gbp is approximately {expected:g}')
def step_holding_mv(context, name: str, expected: float) -> None:
    h = next((h for h in context.mf_sleeve.holdings if h.fund_name == name), None)
    assert h is not None, f"holding {name!r} not found"
    assert _approx(h.market_value_gbp, expected), (
        f"{name} market_value_gbp: expected ~{expected}, got {h.market_value_gbp}"
    )


@then('the MF sleeve region_mix_pct "{region}" is approximately {expected:g}')
def step_region_mix(context, region: str, expected: float) -> None:
    mix = context.mf_sleeve.region_mix_pct
    actual = mix.get(region.upper())
    assert actual is not None, f"region {region!r} missing from mix: {mix}"
    assert _approx(actual, expected, tol=1.0), (
        f"region_mix_pct[{region!r}]: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve type_mix_pct "{ftype}" is approximately {expected:g}')
def step_type_mix(context, ftype: str, expected: float) -> None:
    mix = context.mf_sleeve.type_mix_pct
    actual = mix.get(ftype.lower())
    assert actual is not None, f"type {ftype!r} missing from mix: {mix}"
    assert _approx(actual, expected, tol=1.0), (
        f"type_mix_pct[{ftype!r}]: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve warnings mention "{needle}"')
def step_warning(context, needle: str) -> None:
    warnings = context.mf_sleeve.warnings or []
    joined = " || ".join(warnings)
    assert needle.lower() in joined.lower(), (
        f"warnings do not mention {needle!r}: {warnings}"
    )


@then('the MF sleeve projected annual income is approximately {expected:g}')
def step_income(context, expected: float) -> None:
    actual = context.mf_sleeve.projected_annual_income_gbp
    assert _approx(actual, expected, tol=5.0), (
        f"projected_annual_income_gbp: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve monthly SIP is approximately {expected:g}')
def step_sip(context, expected: float) -> None:
    actual = context.mf_sleeve.planned_monthly_sip_gbp
    assert _approx(actual, expected, tol=2.0), (
        f"planned_monthly_sip_gbp: expected ~{expected}, got {actual}"
    )


@then('the MF sleeve sleeve_pct_of_portfolio is approximately {expected:g}')
def step_sleeve_pct(context, expected: float) -> None:
    actual = context.mf_sleeve.sleeve_pct_of_portfolio
    assert actual is not None, "sleeve_pct_of_portfolio is None"
    assert _approx(actual, expected, tol=0.5), (
        f"sleeve_pct_of_portfolio: expected ~{expected}, got {actual}"
    )
