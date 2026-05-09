"""Steps for cross_sectional.feature — pure ranking helper."""
from __future__ import annotations

import json

from behave import given, then, when

from tradepro_strategies.cross_sectional import rank_by_momentum


@given("the basket json {payload}")
def step_basket(context, payload: str):
    # Convert JS-style null in scenario text to Python None via JSON.
    context.basket = json.loads(payload)


@given("a basket of {n:d} symbols with monotonically decreasing returns")
def step_monotonic(context, n: int):
    context.basket = {f"S{i}": float(n - i) * 5.0 for i in range(n)}


@given("a basket of one symbol")
def step_one(context):
    context.basket = {"ALONE": 12.5}


@when("I rank the basket by momentum")
def step_rank(context):
    context.ranks = rank_by_momentum(context.basket)


@then('"{symbol}" has rank {expected:d}')
def step_assert_rank(context, symbol: str, expected: int):
    actual = context.ranks[symbol]["rank"]
    assert actual == expected, f"{symbol}: expected rank {expected}, got {actual}"


@then('"{symbol}" rank_pct is {expected:f}')
def step_assert_rank_pct(context, symbol: str, expected: float):
    actual = context.ranks[symbol]["rank_pct"]
    assert abs(actual - expected) < 1e-6, f"{symbol}: expected {expected}, got {actual}"


@then('"{symbol}" zscore is positive')
def step_zscore_positive(context, symbol: str):
    z = context.ranks[symbol]["zscore"]
    assert z is not None and z > 0, f"{symbol} zscore: {z}"


@then('"{symbol}" zscore is negative')
def step_zscore_negative(context, symbol: str):
    z = context.ranks[symbol]["zscore"]
    assert z is not None and z < 0, f"{symbol} zscore: {z}"


@then('"{symbol}" has rank None')
def step_rank_none(context, symbol: str):
    assert context.ranks[symbol]["rank"] is None


@then('"{symbol}" zscore is None')
def step_zscore_none(context, symbol: str):
    assert context.ranks[symbol]["zscore"] is None


@then('"{symbol}" peer_count is {expected:d}')
def step_peer_count(context, symbol: str, expected: int):
    actual = context.ranks[symbol]["peer_count"]
    assert actual == expected, f"{symbol}: expected peer_count {expected}, got {actual}"


@then("exactly {n:d} symbols are flagged top quartile")
def step_top_quartile_count(context, n: int):
    top = [s for s, r in context.ranks.items() if r.get("is_top_quartile")]
    assert len(top) == n, f"expected {n} top-quartile, got {len(top)}: {top}"


@then("the single symbol has zscore 0.0")
def step_single_zero(context):
    only = next(iter(context.ranks.values()))
    assert only["zscore"] == 0.0, only


@given("the yield basket json {payload}")
def step_yield_basket(context, payload: str):
    context.yields = json.loads(payload)


@given("the pe basket json {payload}")
def step_pe_basket(context, payload: str):
    context.pes = json.loads(payload)


@when("I bucket the basket by yield quartile")
def step_yield_bucket(context):
    from tradepro_strategies.cross_sectional import bucket_by_yield_quartile
    context.flags = bucket_by_yield_quartile(context.yields)


@when("I bucket the basket by P/E ratio")
def step_pe_bucket(context):
    from tradepro_strategies.cross_sectional import bucket_by_pe_ratio
    context.flags = bucket_by_pe_ratio(context.pes)


@when("I bucket the basket by valuation")
def step_valuation_bucket(context):
    from tradepro_strategies.cross_sectional import bucket_by_valuation
    context.flags = bucket_by_valuation(context.pes, context.yields)


@then('"{symbol}" has flag "{expected}"')
def step_assert_flag(context, symbol: str, expected: str):
    actual = context.flags[symbol]["flag"]
    assert actual == expected, f"{symbol}: expected {expected!r}, got {actual!r}"


@then('the basis for "{symbol}" mentions the basket median')
def step_basis_mentions_median(context, symbol: str):
    basis = context.flags[symbol].get("basis", "")
    assert "median" in basis, f"basis for {symbol} missing 'median': {basis!r}"


@then('the basis for "{symbol}" mentions "{snippet}"')
def step_basis_mentions(context, symbol: str, snippet: str):
    basis = context.flags[symbol].get("basis", "")
    assert snippet in basis, f"basis for {symbol} missing {snippet!r}: {basis!r}"


@then('the lens used is "{expected}"')
def step_lens_used(context, expected: str):
    # All entries in a single-orchestrator output share the same lens.
    sample = next(iter(context.flags.values()))
    actual = sample.get("lens_used")
    assert actual == expected, f"expected lens_used={expected!r}, got {actual!r}"


# ---- Trace rows ----

@given("a top-quartile momentum signal with zscore {z:f}")
def step_top_q_mom(context, z: float):
    context.cs_momentum = {
        "rank": 1, "peer_count": 4, "zscore": z, "is_top_quartile": True,
        "value": 22.0, "basket_median": 12.0, "metric_name": "momentum_12m_pct",
    }


@given("a below-median momentum signal with zscore {z:f}")
def step_below_median_mom(context, z: float):
    context.cs_momentum = {
        "rank": 4, "peer_count": 4, "zscore": z, "is_top_quartile": False,
        "value": 5.0, "basket_median": 12.0, "metric_name": "momentum_12m_pct",
    }


@given("a mid-basket momentum signal with zscore {z:f}")
def step_mid_mom(context, z: float):
    context.cs_momentum = {
        "rank": 2, "peer_count": 4, "zscore": z, "is_top_quartile": False,
        "value": 14.0, "basket_median": 12.0, "metric_name": "momentum_12m_pct",
    }


@given("a cheap valuation flag")
def step_cheap_val(context):
    context.val_flag = {"flag": "cheap", "yield_pct": 5.0,
                        "basket_median_yield_pct": 2.5,
                        "basis": "yield 5.00% vs basket median 2.50%"}


@given("an expensive valuation flag")
def step_expensive_val(context):
    context.val_flag = {"flag": "expensive", "yield_pct": 0.5,
                        "basket_median_yield_pct": 2.5,
                        "basis": "yield 0.50% vs basket median 2.50%"}


@given("a fair valuation flag")
def step_fair_val(context):
    context.val_flag = {"flag": "fair", "yield_pct": 2.5,
                        "basket_median_yield_pct": 2.5,
                        "basis": "yield 2.50% vs basket median 2.50%"}


@given("no cross-basket signals")
def step_no_cs(context):
    context.cs_momentum = None
    context.val_flag = None


@when("I build cross-basket trace rows")
def step_build_trace(context):
    from tradepro_strategies.cross_sectional import cross_basket_trace_rows
    context.trace_rows = cross_basket_trace_rows(
        getattr(context, "cs_momentum", None),
        getattr(context, "val_flag", None),
    )


@then("there are {n:d} trace rows")
def step_trace_count(context, n: int):
    assert len(context.trace_rows) == n, f"expected {n}, got {len(context.trace_rows)}"


@then('the momentum row has status "{expected}"')
def step_momentum_status(context, expected: str):
    matching = [r for r in context.trace_rows if "momentum" in r["name"].lower()]
    assert matching, "no momentum trace row"
    assert matching[0]["status"] == expected, matching[0]


@then('the valuation row has status "{expected}"')
def step_valuation_status(context, expected: str):
    matching = [r for r in context.trace_rows if "valuation" in r["name"].lower()]
    assert matching, "no valuation trace row"
    assert matching[0]["status"] == expected, matching[0]


@then('the momentum row detail mentions "{snippet}"')
def step_momentum_detail(context, snippet: str):
    matching = [r for r in context.trace_rows if "momentum" in r["name"].lower()]
    assert matching, "no momentum trace row"
    assert snippet in matching[0]["detail"], matching[0]["detail"]
