"""Steps for stamp_duty.feature — pure function tests of fees.py."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.fees import stamp_duty_for_symbol, stamp_duty_summary


@given("the symbols {csv}")
def step_symbols(context, csv: str):
    context.symbols = [s.strip() for s in csv.split(",") if s.strip()]


@when("I resolve their stamp duty rates")
def step_resolve(context):
    context.rates = {s: stamp_duty_for_symbol(s) for s in context.symbols}


@when("I summarise stamp duty for the basket")
def step_summarise(context):
    context.summary = stamp_duty_summary(context.symbols)


@then("every rate is {pct}%")
def step_every_rate(context, pct: str):
    expected = float(pct) / 100.0
    bad = {s: r for s, r in context.rates.items() if abs(r - expected) > 1e-9}
    assert not bad, f"unexpected rates: {bad}"


@then("{n:d} symbols are in the {pct}% group")
def step_group_count(context, n: int, pct: str):
    expected_pct = float(pct)
    matching = [g for g in context.summary["groups"] if abs(g["rate_pct"] - expected_pct) < 1e-9]
    assert matching, f"no group with rate_pct={expected_pct}: {context.summary}"
    assert matching[0]["count"] == n, f"expected {n}, got {matching[0]['count']}"
