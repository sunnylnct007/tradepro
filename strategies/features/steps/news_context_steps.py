"""Steps for news_context.feature."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.news_context import compute_news_context


@given('a sentiment_summary with mean_sentiment {value:g}')
def step_sentiment(context, value: float) -> None:
    context.nc_sentiment_summary = {"mean_sentiment": float(value)}


@given('no sentiment_summary')
def step_no_sentiment(context) -> None:
    context.nc_sentiment_summary = None


@given('no news items')
def step_no_news(context) -> None:
    context.nc_news = []


@given('news items with titles {titles_blob}')
def step_news_titles(context, titles_blob: str) -> None:
    # Accept the chained-quote phrasing:
    #   "Beat on Q1" and "Analyst raises target" and "Guidance lifted" …
    import re
    titles = re.findall(r'"([^"]+)"', titles_blob)
    context.nc_news = [{"title": t} for t in titles]


@given('news-context earnings_days is None')
def step_earnings_none(context) -> None:
    context.nc_earnings_days = None


@given('news-context earnings_days is {days:d}')
def step_earnings_days(context, days: int) -> None:
    context.nc_earnings_days = days


@when('I compute news context')
def step_compute(context) -> None:
    context.nc = compute_news_context(
        sentiment_summary=context.nc_sentiment_summary,
        news_items=context.nc_news,
        earnings_proximity_days=context.nc_earnings_days,
    )


@then('the sentiment_score is approximately {expected:g}')
def step_check_score(context, expected: float) -> None:
    assert context.nc.sentiment_score is not None, "sentiment_score is None"
    diff = abs(context.nc.sentiment_score - expected)
    assert diff < 0.05, (
        f"sentiment_score: expected ~{expected}, got "
        f"{context.nc.sentiment_score}"
    )


@then('the sentiment_score is null')
def step_check_score_null(context) -> None:
    assert context.nc.sentiment_score is None, (
        f"sentiment_score: expected None, got {context.nc.sentiment_score}"
    )


@then('the sentiment_trend is "{expected}"')
def step_check_trend(context, expected: str) -> None:
    assert context.nc.sentiment_trend == expected, (
        f"sentiment_trend: expected {expected!r}, got {context.nc.sentiment_trend!r}"
    )


@then('the suppress_signal flag is {flag}')
def step_check_suppress(context, flag: str) -> None:
    want = flag == "True"
    assert context.nc.suppress_signal == want, (
        f"suppress_signal: expected {want}, got {context.nc.suppress_signal}"
    )


@then('the suppress_reason mentions "{needle}"')
def step_check_suppress_reason(context, needle: str) -> None:
    actual = context.nc.suppress_reason or ""
    assert needle.lower() in actual.lower(), (
        f"suppress_reason {actual!r} does not mention {needle!r}"
    )


@then('the suppress_reason is null')
def step_check_suppress_reason_null(context) -> None:
    assert context.nc.suppress_reason is None, (
        f"suppress_reason: expected None, got {context.nc.suppress_reason!r}"
    )


@then('the key_headlines list has {n:d} entries')
def step_check_headline_count(context, n: int) -> None:
    assert len(context.nc.key_headlines) == n, (
        f"key_headlines: expected {n} entries, got "
        f"{len(context.nc.key_headlines)}: {context.nc.key_headlines!r}"
    )


@then('the first key_headline is "{expected}"')
def step_check_first_headline(context, expected: str) -> None:
    assert context.nc.key_headlines, "key_headlines is empty"
    actual = context.nc.key_headlines[0]
    assert actual == expected, f"first headline: expected {expected!r}, got {actual!r}"
