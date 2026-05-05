"""Steps for swing.feature — composite scorer is pure-function so
no fixtures touch the network."""
from __future__ import annotations

from behave import given, then, when
from behave.matchers import use_step_matcher

from tradepro_strategies.swing import evaluate_swing

use_step_matcher("parse")


@given("a row with Sharpe {sharpe:g} and max-DD recovery {days:d}d")
def step_quality_normal(context, sharpe: float, days: int):
    context.row = {
        "stats": {
            "sharpe": sharpe,
            "max_drawdown_pct": -22.0,
            "max_drawdown_recovery_days": days,
            "max_drawdown_still_recovering": False,
        },
    }


@given("a row with Sharpe {sharpe:g} and still recovering from drawdown")
def step_quality_still_recovering(context, sharpe: float):
    context.row = {
        "stats": {
            "sharpe": sharpe,
            "max_drawdown_pct": -45.0,
            "max_drawdown_recovery_days": None,
            "max_drawdown_still_recovering": True,
        },
    }


@given("the row has valuation flag {flag}")
def step_valuation_flag(context, flag: str):
    context.row["valuation_flag"] = {
        "flag": flag,
        "yield_pct": 4.0 if flag == "cheap" else (1.0 if flag == "expensive" else 2.5),
        "basket_median_yield_pct": 2.5,
        "basis": f"yield X% vs basket median 2.50% (flag={flag})",
    }


@given("the row has a STRONG beat-and-retreat earnings signal")
def step_earnings_strong(context):
    context.row["earnings_signal"] = {
        "verdict": "STRONG",
        "fired": True,
        "retreat_from_post_earnings_peak_pct": -8.5,
        "earnings": {"surprise_pct": 5.0},
    }


@given("the row has no recent earnings event")
def step_earnings_none(context):
    context.row["earnings_signal"] = {"verdict": "NO_RECENT"}


@given('the row has earnings verdict "{verdict}"')
def step_earnings_verdict(context, verdict: str):
    context.row["earnings_signal"] = {"verdict": verdict}


@given("the row has {long:d} of {total:d} strategies long with RSI {rsi:g} above SMA200")
def step_price_above(context, long: int, total: int, rsi: float):
    context.row["long_count"] = long
    context.row["total_strategies"] = total
    context.row["market_state"] = {
        "rsi_14": rsi,
        "above_sma_200": True,
    }


@given("the row has {long:d} of {total:d} strategies long with RSI {rsi:g} below SMA200")
def step_price_below(context, long: int, total: int, rsi: float):
    context.row["long_count"] = long
    context.row["total_strategies"] = total
    context.row["market_state"] = {
        "rsi_14": rsi,
        "above_sma_200": False,
    }


@when("I score the row's swing setup")
def step_score(context):
    context.score = evaluate_swing(context.row)


@then("the swing total is {expected:d}")
def step_assert_total(context, expected: int):
    assert context.score.total == expected, (
        f"expected total {expected}, got {context.score.total} "
        f"(layers={context.score.layers})"
    )


@then('the swing verdict is "{expected}"')
def step_assert_verdict(context, expected: str):
    assert context.score.verdict == expected, (
        f"expected {expected!r}, got {context.score.verdict!r} "
        f"(total={context.score.total}, layers={context.score.layers})"
    )


@then("the event layer score is {expected:d}")
def step_event_score(context, expected: int):
    assert context.score.layers["event"] == expected, context.score.layers


@then('the event reason mentions "{snippet}"')
def step_event_reason(context, snippet: str):
    reason = context.score.reasons.get("event", "")
    assert snippet in reason, f"event reason {reason!r} missing {snippet!r}"
