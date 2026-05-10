"""Steps for email_charts.feature — pin the chart-helper contracts the
HTML digest depends on.

We only assert the data URL shape (empty vs base64 PNG header). We
don't decode and inspect pixels — that would couple tests to
matplotlib version drift without catching real regressions."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.email_charts import (
    bucket_donut_png,
    buy_sparklines_png,
)


@given("bucket counts buy={buy:d} wait={wait:d} avoid={avoid:d}")
def step_bucket_counts(context, buy: int, wait: int, avoid: int) -> None:
    context.buy = buy
    context.wait = wait
    context.avoid = avoid


@when("I render the bucket donut")
def step_render_donut(context) -> None:
    context.url = bucket_donut_png(context.buy, context.wait, context.avoid)


@given('one BUY item "{symbol}" with recent_closes of 30 ascending floats')
def step_item_recent_closes(context, symbol: str) -> None:
    context.items = [{
        "symbol": symbol,
        "recent_closes": [100.0 + i for i in range(30)],
    }]


@given('one BUY item "{symbol}" with no recent_closes but market_state.closes_30d of 30 floats')
def step_item_market_state_fallback(context, symbol: str) -> None:
    context.items = [{
        "symbol": symbol,
        "market_state": {"closes_30d": [200.0 + i * 0.5 for i in range(30)]},
    }]


@given("one BUY item with no series and one BUY item with only 3 floats")
def step_item_no_usable_series(context) -> None:
    context.items = [
        {"symbol": "EMPTY"},
        {"symbol": "TINY", "recent_closes": [1.0, 2.0, 3.0]},
    ]


@given("{count:d} BUY items each with 30-float close series")
def step_many_items(context, count: int) -> None:
    context.items = [
        {"symbol": f"S{i}", "recent_closes": [100.0 + j for j in range(30)]}
        for i in range(count)
    ]


@when("I render the BUY sparklines")
def step_render_sparklines(context) -> None:
    context.url = buy_sparklines_png(context.items)


@then("the data URL is empty")
def step_url_empty(context) -> None:
    assert context.url == "", f"expected empty data URL, got {context.url[:80]!r}…"


@then("the data URL is a base64 PNG")
def step_url_png(context) -> None:
    assert context.url.startswith("data:image/png;base64,"), (
        f"expected base64 PNG data URL, got {context.url[:80]!r}…"
    )
    # Sanity: there's actual payload, not just the header.
    assert len(context.url) > len("data:image/png;base64,") + 100
