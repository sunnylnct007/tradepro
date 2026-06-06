"""Steps for asset_class_resolver.feature — pins every ticker rule
the rule-based resolver exposes. Pure functions, no I/O."""
from __future__ import annotations

from behave import then, when

from tradepro_strategies.bar_cache.asset_class_resolver import (
    resolve_asset_class,
    resolve_many,
)


@when('the resolver is asked for "{ticker}"')
def step_resolve_string(context, ticker):
    context.asset_class = resolve_asset_class(ticker)


@when('the resolver is asked for None')
def step_resolve_none(context):
    context.asset_class = resolve_asset_class(None)


@then('the asset class is "{expected}"')
def step_assert_asset_class(context, expected):
    assert context.asset_class == expected, (
        f"expected {expected!r}, got {context.asset_class!r}"
    )


@when('the resolver runs on tickers {tickers}')
def step_resolve_many(context, tickers):
    ticker_list = tickers.split()
    context.mapping = resolve_many(ticker_list)


@then('the resolver result has {n:d} entries')
def step_assert_count(context, n):
    assert len(context.mapping) == n, (
        f"expected {n} entries, got {len(context.mapping)}: {context.mapping}"
    )


@then('the mapping {ticker} is "{expected}"')
def step_assert_mapping(context, ticker, expected):
    got = context.mapping.get(ticker)
    assert got == expected, (
        f"mapping[{ticker!r}] = {got!r}, expected {expected!r}"
    )
