"""Steps for gems.feature — Phase G gem hunter."""
from __future__ import annotations

import re

from behave import given, then, when

from tradepro_strategies.gems import evaluate_gem


_ROW_PATTERN = re.compile(
    r"(?P<sharpe>[-+]?\d*\.?\d+),\s*"
    r"recovers in (?P<rec_months>\d+)mo,\s*"
    r"(?P<dd>[-+]?\d*\.?\d+)% from 5y peak,\s*"
    r"(?P<rp>\d+)(?:st|nd|rd|th)? pctile,\s*"
    r"(?P<flag>CHEAP|FAIR|EXPENSIVE|cheap|fair|expensive),\s*"
    r"RSI (?P<rsi>\d+),\s*"
    r"(?P<sma>above|below) SMA200,\s*"
    r"sentiment (?P<sent>[-+]?\d*\.?\d+)"
)


# Unique prefix so behave's step matcher doesn't collide with risk_steps'
# "a row with vol …" or swing_steps' "a row with Sharpe …".
@given("a gem-profile row: Sharpe {desc}")
def step_row(context, desc: str):
    m = _ROW_PATTERN.search(desc)
    if not m:
        raise AssertionError(f"could not parse row description: {desc!r}")
    g = m.groupdict()
    context.row = {
        "symbol": "TEST",
        "market_state": {
            "rsi_14": float(g["rsi"]),
            "drawdown_from_peak_pct": float(g["dd"]),
            "range_position_pct": float(g["rp"]),
            "above_sma_200": g["sma"] == "above",
            "last_price": 100.0,
        },
        "stats": {
            "sharpe": float(g["sharpe"]),
            "max_drawdown_recovery_days": int(g["rec_months"]) * 30,
        },
        "sentiment_summary": {
            "mean_sentiment": float(g["sent"]),
        },
        "fundamentals": {"n_holdings": 100, "legal_type": "ETF"},
        "valuation_flag": {"flag": g["flag"].lower(), "basis": f"P/E quartile flag {g['flag'].lower()}"},
        "cross_sectional_momentum": {"zscore": 0.0},
    }


@when("I evaluate it as a gem")
def step_eval(context):
    context.verdict = evaluate_gem(context.row)


@then("it is a gem")
def step_is_gem(context):
    assert context.verdict.is_gem, (
        f"expected gem, got fail. failed_filters={context.verdict.reasons.failed_filters}"
    )


@then("it is NOT a gem")
def step_not_gem(context):
    assert not context.verdict.is_gem, (
        f"expected not-a-gem, got is_gem=True. passing={context.verdict.reasons.all_passing()}"
    )


@then('the passing reasons mention "{snippet}"')
def step_passing_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.all_passing())
    assert found, f"passing reasons missing {snippet!r}: {context.verdict.reasons.all_passing()}"


@then('the recovery signals mention "{snippet}"')
def step_recovery_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.recovery_signals)
    assert found, f"recovery signals missing {snippet!r}: {context.verdict.reasons.recovery_signals}"


@then('the failed filters mention "{snippet}"')
def step_failed_mentions(context, snippet: str):
    found = any(snippet in r for r in context.verdict.reasons.failed_filters)
    assert found, f"failed filters missing {snippet!r}: {context.verdict.reasons.failed_filters}"
