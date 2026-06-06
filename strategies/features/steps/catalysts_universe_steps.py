"""Steps for catalysts_universe.feature — exercises the priority +
dedup + normalisation rules in assemble_universe."""
from __future__ import annotations

import ast

from behave import given, then, when

from tradepro_strategies.catalysts_universe import assemble_universe


def _parse_list(raw: str) -> list[str]:
    """Accept Python list literal so the feature file can write
    ['AAPL', 'MSFT'] verbatim."""
    return list(ast.literal_eval(raw))


@given('positions {positions} and watchlists {watchlists} '
       'and trader_universe {trader_universe}')
def step_set_inputs(context, positions, watchlists, trader_universe):
    context.positions = _parse_list(positions)
    context.watchlists = _parse_list(watchlists)
    context.trader_universe = _parse_list(trader_universe)


@when('the universe is assembled')
def step_assemble(context):
    context.report = assemble_universe(
        positions=context.positions,
        watchlists=context.watchlists,
        trader_universe=context.trader_universe,
        include_baseline_etfs=False,
        include_baseline_fx=False,
    )


@when('the universe is assembled with baseline_etfs on and baseline_fx off')
def step_assemble_etfs_only(context):
    context.report = assemble_universe(
        positions=context.positions,
        watchlists=context.watchlists,
        trader_universe=context.trader_universe,
        include_baseline_etfs=True,
        include_baseline_fx=False,
    )


@when('the universe is assembled with baselines off')
def step_assemble_no_baselines(context):
    context.report = assemble_universe(
        positions=context.positions,
        watchlists=context.watchlists,
        trader_universe=context.trader_universe,
        include_baseline_etfs=False,
        include_baseline_fx=False,
    )


@then('{symbol} is attributed to "{expected_source}"')
def step_assert_attribution(context, symbol, expected_source):
    for source, syms in context.report.by_source.items():
        if symbol in syms:
            assert source == expected_source, (
                f"{symbol} attributed to {source!r}, expected {expected_source!r}"
            )
            return
    raise AssertionError(
        f"{symbol} missing from universe (by_source = {context.report.by_source})"
    )


@then('the total symbol count is {n:d}')
def step_assert_total(context, n):
    assert context.report.total == n, (
        f"expected {n} symbols, got {context.report.total}: {context.report.symbols}"
    )


@then('the source "{name}" exists')
def step_assert_source_exists(context, name):
    assert name in context.report.by_source, (
        f"source {name!r} missing from {list(context.report.by_source)}"
    )


@then('the source "{name}" does not exist')
def step_assert_source_missing(context, name):
    assert name not in context.report.by_source, (
        f"source {name!r} unexpectedly present"
    )


@then('the symbol order is "{expected}"')
def step_assert_order(context, expected):
    expected_tuple = tuple(expected.split())
    assert context.report.symbols == expected_tuple, (
        f"expected order {expected_tuple}, got {context.report.symbols}"
    )
