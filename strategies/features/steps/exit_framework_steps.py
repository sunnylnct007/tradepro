"""Steps for exit_framework.feature — pins the contract on
compute_exit_levels / gate_check_rr / compute_position_sizing."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.exit_framework import (
    compute_exit_levels,
    compute_position_sizing,
    gate_check_rr,
)


def _f(s: str) -> float | None:
    s = s.strip()
    if s.lower() == "none":
        return None
    return float(s)


@given('an entry price of {entry:f}')
def step_entry(context, entry: float) -> None:
    context.entry = entry
    context.rr_ratio = 2.0


@given('an entry price of {entry:f} with a custom rr_ratio of {rr:f}')
def step_entry_custom_rr(context, entry: float, rr: float) -> None:
    context.entry = entry
    context.rr_ratio = rr


@given('atr_14 is {atr:S}')
def step_atr(context, atr: str) -> None:
    context.atr_14 = _f(atr)


@given('the strategy_type is "{stype}"')
def step_stype(context, stype: str) -> None:
    context.strategy_type = stype


@when('I compute exit levels')
def step_compute(context) -> None:
    context.levels = compute_exit_levels(
        entry_price=context.entry,
        atr_14=context.atr_14,
        strategy_type=context.strategy_type,
        rr_ratio=getattr(context, "rr_ratio", 2.0),
    )


@then('the method is "{expected}"')
def step_check_method(context, expected: str) -> None:
    assert context.levels is not None, "exit levels are None"
    assert context.levels.method == expected, (
        f"method: expected {expected!r}, got {context.levels.method!r}"
    )


@then('the stop_loss is approximately {expected:f}')
def step_check_stop(context, expected: float) -> None:
    assert context.levels is not None, "exit levels are None"
    actual = context.levels.stop_loss
    assert abs(actual - expected) < 0.05, (
        f"stop_loss: expected ~{expected}, got {actual}"
    )


@then('the take_profit is approximately {expected:f}')
def step_check_target(context, expected: float) -> None:
    assert context.levels is not None, "exit levels are None"
    actual = context.levels.take_profit
    assert abs(actual - expected) < 0.05, (
        f"take_profit: expected ~{expected}, got {actual}"
    )


@then('the rr_ratio is {expected:f}')
def step_check_rr(context, expected: float) -> None:
    assert context.levels is not None, "exit levels are None"
    assert abs(context.levels.rr_ratio - expected) < 0.01, (
        f"rr_ratio: expected {expected}, got {context.levels.rr_ratio}"
    )


@then('there are no exit levels')
def step_no_levels(context) -> None:
    assert context.levels is None, f"expected None, got {context.levels!r}"


@when('I check the RR gate')
def step_check_gate(context) -> None:
    context.gate_pass, context.gate_reason = gate_check_rr(context.levels)


@then('the gate passes')
def step_gate_pass(context) -> None:
    assert context.gate_pass, f"gate failed: {context.gate_reason}"


@then('the gate fails')
def step_gate_fail(context) -> None:
    assert not context.gate_pass, "gate passed but expected failure"


@then('the gate reason mentions "{needle}"')
def step_gate_reason(context, needle: str) -> None:
    assert needle.lower() in (context.gate_reason or "").lower(), (
        f"gate reason {context.gate_reason!r} does not mention {needle!r}"
    )


# ─────────── position sizing ───────────


@given('an account size of {size:g} GBP with {risk:g} percent risk per trade')
def step_account(context, size: float, risk: float) -> None:
    context.account_size_gbp = float(size)
    context.risk_per_trade_pct = float(risk) / 100.0


@given('the entry price is {price:f} USD')
def step_size_entry(context, price: float) -> None:
    context.size_entry = price


@given('the stop distance is {dist:f} USD')
def step_size_stop(context, dist: float) -> None:
    context.size_stop_distance = dist


@given('the FX rate is {fx:f} GBPUSD')
def step_size_fx(context, fx: float) -> None:
    context.size_fx = fx


@when('I compute position sizing')
def step_compute_sizing(context) -> None:
    context.sizing = compute_position_sizing(
        entry_price_usd=context.size_entry,
        stop_distance_usd=context.size_stop_distance,
        account_size_gbp=context.account_size_gbp,
        risk_per_trade_pct=context.risk_per_trade_pct,
        fx_rate_gbpusd=context.size_fx,
    )


@then('the suggested shares is {expected:d}')
def step_check_shares(context, expected: int) -> None:
    assert context.sizing is not None, "sizing is None"
    assert context.sizing.suggested_shares == expected, (
        f"shares: expected {expected}, got {context.sizing.suggested_shares}"
    )


@then('the max_loss_gbp is approximately {expected:f}')
def step_check_max_loss(context, expected: float) -> None:
    assert context.sizing is not None, "sizing is None"
    actual = context.sizing.max_loss_gbp
    assert abs(actual - expected) < 0.5, (
        f"max_loss_gbp: expected ~{expected}, got {actual}"
    )


@then('there is no position sizing')
def step_no_sizing(context) -> None:
    assert context.sizing is None, f"expected None, got {context.sizing!r}"
