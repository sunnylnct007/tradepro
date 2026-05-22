"""Steps for exit_framework.feature — pins the contract on
compute_exit_levels / gate_check_rr / compute_position_sizing."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.exit_framework import (
    build_ibkr_order_instructions,
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


# ─────────── IBKR order instructions ───────────


@given('a {direction} direction with entry {entry:g} stop {stop:g} target {target:g} quantity {qty:d}')
def step_ibkr_inputs(context, direction: str, entry: float, stop: float, target: float, qty: int) -> None:
    context.ibkr_direction = direction
    context.ibkr_entry = float(entry)
    context.ibkr_stop = float(stop)
    context.ibkr_target = float(target)
    context.ibkr_qty = qty


@when('I build the IBKR order instructions')
def step_build_ibkr(context) -> None:
    context.ibkr = build_ibkr_order_instructions(
        direction=context.ibkr_direction,
        entry_price=context.ibkr_entry,
        stop_loss=context.ibkr_stop,
        take_profit=context.ibkr_target,
        quantity=context.ibkr_qty,
    )


@then('the entry_order action is "{action}" with quantity {qty:d} and limit_price {price:g}')
def step_check_entry_order(context, action: str, qty: int, price: float) -> None:
    eo = context.ibkr.get("entry_order") or {}
    assert eo.get("action") == action, f"entry action: expected {action}, got {eo.get('action')}"
    assert eo.get("quantity") == qty, f"entry qty: expected {qty}, got {eo.get('quantity')}"
    assert abs(eo.get("limit_price", 0) - price) < 0.01, (
        f"entry limit_price: expected {price}, got {eo.get('limit_price')}"
    )


@then('the profit_taker action is "{action}" with quantity {qty:d} and limit_price {price:g}')
def step_check_profit_taker(context, action: str, qty: int, price: float) -> None:
    pt = context.ibkr.get("profit_taker") or {}
    assert pt.get("action") == action
    assert pt.get("quantity") == qty
    assert abs(pt.get("limit_price", 0) - price) < 0.01


@then('the stop_loss action is "{action}" with quantity {qty:d} and stop_price {price:g}')
def step_check_stop_loss(context, action: str, qty: int, price: float) -> None:
    sl = context.ibkr.get("stop_loss") or {}
    assert sl.get("action") == action
    assert sl.get("quantity") == qty
    assert abs(sl.get("stop_price", 0) - price) < 0.01


@then('the oca_required flag is {flag}')
def step_check_oca(context, flag: str) -> None:
    want = flag == "True"
    assert context.ibkr.get("oca_required") == want, (
        f"oca_required: expected {want}, got {context.ibkr.get('oca_required')}"
    )


@then('the instructions contain a refusal note mentioning "{needle}"')
def step_check_refusal(context, needle: str) -> None:
    note = (context.ibkr or {}).get("note", "")
    assert needle.lower() in note.lower(), (
        f"refusal note {note!r} does not mention {needle!r}"
    )
    assert "entry_order" not in (context.ibkr or {}), (
        "refused IBKR card should not carry an entry_order block"
    )
