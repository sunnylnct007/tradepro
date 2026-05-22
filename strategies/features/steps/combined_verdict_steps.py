"""Steps for combined_verdict.feature — fuse the three layers and pin
the rule table. Real-trade samples land here as new scenarios; the
helper `_build_row` keeps the fixture geometry concise."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from behave import given, then, when

from tradepro_strategies.catalysts import extract_catalysts
from tradepro_strategies.combined_verdict import derive_combined_verdict


@given('a row with bucket "{bucket}" and reason "{reason}"')
def step_row_bucket(context, bucket: str, reason: str) -> None:
    context.row = {
        "bucket": bucket,
        "bucket_reason": reason,
        "news": [],
        "analyst_recommendations": {},
    }


@given("the news headlines:")
def step_news_table(context) -> None:
    """Behave table — columns: title, sentiment, days_offset.
    days_offset is relative to today (0=today, -2=two days ago)."""
    today = datetime.now(timezone.utc)
    news = []
    for row in context.table:
        try:
            offset = int(row["days_offset"])
        except (KeyError, ValueError):
            offset = 0
        published = (today + timedelta(days=offset)).isoformat()
        try:
            sentiment = float(row["sentiment"])
        except (KeyError, ValueError, TypeError):
            sentiment = None
        news.append({
            "title": row["title"],
            "sentiment": sentiment,
            "published_at": published,
            "link": None,
        })
    context.row["news"] = news
    # Catalysts are derived from news the same way compare.py does it.
    context.row["catalysts"] = [c.to_dict() for c in extract_catalysts(news)]


@given("analyst counts strong_buy={sb:d} buy={b:d} hold={h:d} sell={s:d} strong_sell={ss:d}")
def step_analyst(context, sb: int, b: int, h: int, s: int, ss: int) -> None:
    context.row["analyst_recommendations"] = {
        "strong_buy": sb,
        "buy": b,
        "hold": h,
        "sell": s,
        "strong_sell": ss,
        "bull_score": (sb + b) - (s + ss),
    }


@when("I derive the combined verdict")
def step_derive(context) -> None:
    context.verdict = derive_combined_verdict(context.row)


@then('the technical signal is "{expected}"')
def step_technical_signal(context, expected: str) -> None:
    actual = context.verdict["technical"]["signal"]
    assert actual == expected, f"technical.signal: expected {expected!r}, got {actual!r}"


@then('the catalyst signal is "{expected}"')
def step_catalyst_signal(context, expected: str) -> None:
    actual = context.verdict["catalyst"]["signal"]
    assert actual == expected, f"catalyst.signal: expected {expected!r}, got {actual!r}"


@then('the analyst signal is "{expected}"')
def step_analyst_signal(context, expected: str) -> None:
    actual = context.verdict["analyst"]["signal"]
    assert actual == expected, f"analyst.signal: expected {expected!r}, got {actual!r}"


@then('the combined_kind is "{expected}"')
def step_combined_kind(context, expected: str) -> None:
    actual = context.verdict["combined_kind"]
    assert actual == expected, (
        f"combined_kind: expected {expected!r}, got {actual!r}\n"
        f"  combined label: {context.verdict['combined']!r}\n"
        f"  reasoning: {context.verdict['reasoning']!r}"
    )


@then('the confidence is "{expected}"')
def step_confidence(context, expected: str) -> None:
    actual = context.verdict["confidence"]
    assert actual == expected, f"confidence: expected {expected!r}, got {actual!r}"


@then('the reasoning mentions "{snippet}"')
def step_reasoning_mentions(context, snippet: str) -> None:
    lines = context.verdict["reasoning"]
    found = any(snippet in line for line in lines)
    assert found, f"reasoning missing {snippet!r}: {lines!r}"
