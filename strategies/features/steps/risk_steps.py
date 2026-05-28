"""Steps for risk.feature — Phase R risk rating engine."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.risk import compute_risk_rating, position_cap_pct


def _row(**ms) -> dict:
    """Helper: pack market_state kwargs into the row shape the rater
    consumes."""
    out: dict = {"market_state": {}, "stats": {}}
    for k, v in ms.items():
        if k == "vol":
            if v is not None:
                out["market_state"]["vol_30d_annual_pct"] = v
        elif k == "rec_days":
            out["stats"]["max_drawdown_recovery_days"] = v
        elif k == "mat_neg":
            out.setdefault("sentiment_summary", {})["material_negative_count"] = v
        elif k == "rp":
            out["market_state"]["range_position_pct"] = v
        elif k == "bucket":
            out["bucket"] = v
        elif k == "z":
            out["cross_sectional_momentum"] = {"zscore": v}
    return out


@given("a row with vol {pct:g}% and no escalators")
def step_row_vol_only(context, pct: float):
    context.row = _row(vol=pct)


@given("a row with no vol data")
def step_row_no_vol(context):
    context.row = _row()


@given("a row with vol {pct:g}% and {days:d}-day historical DD recovery")
def step_row_vol_rec(context, pct: float, days: int):
    context.row = _row(vol=pct, rec_days=days)


@given("a row with vol {pct:g}% and BUY at {rp:g}th pctile of 52w range")
def step_row_vol_pctile(context, pct: float, rp: float):
    context.row = _row(vol=pct, bucket="BUY", rp=rp)


@given("a row with vol {pct:g}% and {n:d} material-negative headlines in 7d")
def step_row_vol_matneg(context, pct: float, n: int):
    context.row = _row(vol=pct, mat_neg=n)


@given("a row with vol {pct:g}%, {days:d}-day DD recovery, {n:d} material-negatives, BUY at {rp:g}th pctile, z-score {z:g}")
def step_row_all(context, pct: float, days: int, n: int, rp: float, z: float):
    context.row = _row(vol=pct, rec_days=days, mat_neg=n, bucket="BUY", rp=rp, z=z)


@given('a "{rating}" risk rating')
def step_rating(context, rating: str):
    context.rating = rating


@when("I compute the risk rating")
def step_compute(context):
    context.result = compute_risk_rating(context.row)


@when("I look up the position cap")
def step_cap(context):
    context.cap = position_cap_pct(context.rating)


@then('the rating is "{expected}"')
def step_rating_eq(context, expected: str):
    actual = context.result.rating
    assert actual == expected, (
        f"rating: expected {expected!r}, got {actual!r} "
        f"(baseline={context.result.baseline}, factors={context.result.factors})"
    )


@then('the baseline is "{expected}"')
def step_baseline_eq(context, expected: str):
    actual = context.result.baseline
    assert actual == expected, f"baseline: expected {expected!r}, got {actual!r}"


@then("the escalators count is {n:d}")
def step_escalators_count(context, n: int):
    actual = context.result.escalators
    assert actual == n, (
        f"escalators: expected {n}, got {actual} "
        f"(factors={context.result.factors})"
    )


@then('the factors mention "{snippet}"')
def step_factors_mention(context, snippet: str):
    factors = context.result.factors
    found = any(snippet in f for f in factors)
    assert found, f"factors missing {snippet!r}: {factors}"


@then("the cap is {expected:f}")
def step_cap_eq(context, expected: float):
    assert abs(context.cap - expected) < 0.001, (
        f"cap: expected {expected}, got {context.cap}"
    )
