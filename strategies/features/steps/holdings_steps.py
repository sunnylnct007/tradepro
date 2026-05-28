"""Steps for holdings.feature — Phase-2 portfolio-aware engine."""
from __future__ import annotations

from behave import given, then, when
from behave.matchers import use_step_matcher

from tradepro_strategies.holdings import analyse_holding

use_step_matcher("parse")


@given("a holding worth {qty:d} shares of {symbol} bought at {avg:g} {ccy} now at {cur:g} {ccy2}")
def step_holding(context, qty: int, symbol: str, avg: float, ccy: str, cur: float, ccy2: str):
    upct = (cur - avg) / avg * 100.0 if avg > 0 else 0.0
    context.holding = {
        "yahooSymbol": symbol,
        "ticker": f"{symbol}_US_EQ",
        "instrumentName": symbol,
        "currency": ccy,
        "quantity": float(qty),
        "averagePricePaid": avg,
        "currentPrice": cur,
        "unrealisedPct": upct,
        "unrealisedAbs": (cur - avg) * qty,
    }


@given("today's row says {bucket} with swing {total:d}/8 {verdict}")
def step_row_bucket_swing(context, bucket: str, total: int, verdict: str):
    context.row = {
        "symbol": context.holding["yahooSymbol"],
        "bucket": bucket,
        "swing_score": {
            "total": total,
            "verdict": verdict,
            "layers": {"quality": 1, "valuation": 1, "event": 0, "price": 1},
            "reasons": {},
        },
        "market_state": {},
    }


@given("the row's RSI is {rsi:g} above 200d SMA")
def step_rsi_above(context, rsi: float):
    context.row["market_state"]["rsi_14"] = rsi
    context.row["market_state"]["above_sma_200"] = True


@given("the row's RSI is {rsi:g} below 200d SMA")
def step_rsi_below(context, rsi: float):
    context.row["market_state"]["rsi_14"] = rsi
    context.row["market_state"]["above_sma_200"] = False


@given("the holding is down {pct:g}%")
def step_holding_down(context, pct: float):
    avg = context.holding["averagePricePaid"]
    cur = avg * (1 - pct / 100.0)
    context.holding["currentPrice"] = cur
    context.holding["unrealisedPct"] = -pct
    context.holding["unrealisedAbs"] = (cur - avg) * context.holding["quantity"]


@given("the holding is up {pct:g}%")
def step_holding_up(context, pct: float):
    avg = context.holding["averagePricePaid"]
    cur = avg * (1 + pct / 100.0)
    context.holding["currentPrice"] = cur
    context.holding["unrealisedPct"] = pct
    context.holding["unrealisedAbs"] = (cur - avg) * context.holding["quantity"]


@given("no compare row for the holding")
def step_no_row(context):
    context.row = None


@when("I analyse the holding")
def step_analyse(context):
    context.rec = analyse_holding(context.holding, getattr(context, "row", None))


@when('I analyse the holding with horizon "{horizon}"')
def step_analyse_horizon(context, horizon: str):
    context.rec = analyse_holding(
        context.holding, getattr(context, "row", None), horizon=horizon,
    )


@then('the action is "{expected}"')
def step_action(context, expected: str):
    assert context.rec.action == expected, (
        f"expected {expected!r}, got {context.rec.action!r} "
        f"(narrative: {context.rec.narrative!r})"
    )


@then('the narrative mentions "{snippet}"')
def step_narrative(context, snippet: str):
    assert snippet.lower() in context.rec.narrative.lower(), (
        f"narrative {context.rec.narrative!r} missing {snippet!r}"
    )


@then("the new cost basis is reported")
def step_new_cost(context):
    assert context.rec.avg_cost_after_equal_tranche is not None, (
        "BUY_MORE should report the post-tranche average cost"
    )
