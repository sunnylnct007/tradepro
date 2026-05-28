"""Steps for entry_timing.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.core_portfolio import (
    QualityScorecard,
    ValuationLayer,
    compute_entry_timing,
)


@given('a quality scorecard with stars {n:d}')
def step_quality(context, n: int) -> None:
    context.et_quality = QualityScorecard(
        symbol="TEST",
        stars=n,
        overall_score=float(n * 2),
        metrics=[],
        missing_metrics=[],
        source="test",
    )


@given('no quality scorecard')
def step_no_quality(context) -> None:
    context.et_quality = None


@given('a valuation layer with verdict "{verdict}"')
def step_valuation(context, verdict: str) -> None:
    context.et_valuation = ValuationLayer(
        symbol="TEST",
        overall_verdict=verdict,
        metrics=[],
        missing_metrics=[],
        source="test",
        rationale="",
    )


@given('drawdown of {pct:g} percent from 52w high')
def step_drawdown(context, pct: float) -> None:
    context.et_drawdown = float(pct)
    context.et_market_state = None


@given('a market_state with pct_off_52w_high_pct {pct:g}')
def step_market_state(context, pct: float) -> None:
    context.et_market_state = {"pct_off_52w_high_pct": float(pct)}
    context.et_drawdown = None


@given('a long-term grade "{grade}"')
def step_grade(context, grade: str) -> None:
    context.et_long_term_grade = grade


@when('I compute entry timing for "{symbol}"')
def step_compute(context, symbol: str) -> None:
    context.et = compute_entry_timing(
        symbol,
        quality=context.et_quality,
        valuation=context.et_valuation,
        market_state=getattr(context, "et_market_state", None),
        drawdown_pct=getattr(context, "et_drawdown", None),
        long_term_grade=getattr(context, "et_long_term_grade", None),
    )


@then('the entry verdict is "{expected}"')
def step_verdict(context, expected: str) -> None:
    assert context.et.verdict == expected, (
        f"verdict: expected {expected!r}, got {context.et.verdict!r} "
        f"(rationale: {context.et.rationale})"
    )


@then('signals_passing is {n:d}')
def step_signals(context, n: int) -> None:
    assert context.et.signals_passing == n, (
        f"signals_passing: expected {n}, got {context.et.signals_passing}"
    )


@then('the entry rationale mentions "{needle}"')
def step_rationale(context, needle: str) -> None:
    assert needle.lower() in (context.et.rationale or "").lower(), (
        f"rationale {context.et.rationale!r} does not mention {needle!r}"
    )


@then('the entry drawdown is approximately {expected:g}')
def step_drawdown_check(context, expected: float) -> None:
    actual = context.et.drawdown_from_52w_high_pct
    assert actual is not None, "drawdown is None"
    assert abs(actual - expected) < 0.5, (
        f"drawdown: expected ~{expected}, got {actual}"
    )


@then('the entry quality_source is "{expected}"')
def step_quality_source(context, expected: str) -> None:
    actual = context.et.quality_source
    assert actual == expected, (
        f"quality_source: expected {expected!r}, got {actual!r}"
    )
