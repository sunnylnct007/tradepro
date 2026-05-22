"""Steps for catalysts.feature — pin the extractor's behaviour.

Single-symbol synthetic news; no Yahoo / network. The extractor is
pure-Python over a list of dicts, so the steps just build dicts and
assert on the returned Catalyst records."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.catalysts import extract_catalysts


@given('a headline "{title}" surfaced on {date}')
def step_one_headline(context, title: str, date: str) -> None:
    # Anchor the headline to local noon UTC on the given date so
    # downstream date math has a deterministic surfaced_at to work
    # from.
    iso = f"{date}T12:00:00Z"
    context.headlines = [{"title": title, "published_at": iso, "link": None}]


@given("the headlines:")
def step_many_headlines(context) -> None:
    """Behave table — columns 'title' and 'published_at'."""
    context.headlines = [
        {
            "title": row["title"],
            "published_at": row["published_at"],
            "link": None,
        }
        for row in context.table
    ]


@when("I extract catalysts")
def step_extract(context) -> None:
    context.catalysts = extract_catalysts(context.headlines)


@then("exactly {n:d} catalyst is returned")
def step_count_singular(context, n: int) -> None:
    actual = len(context.catalysts)
    assert actual == n, f"expected {n} catalyst, got {actual}: {context.catalysts!r}"


@then("exactly {n:d} catalysts are returned")
def step_count_plural(context, n: int) -> None:
    actual = len(context.catalysts)
    assert actual == n, f"expected {n} catalysts, got {actual}: {context.catalysts!r}"


@then('catalyst {idx:d} has kind "{kind}"')
def step_kind(context, idx: int, kind: str) -> None:
    actual = context.catalysts[idx].kind
    assert actual == kind, f"catalyst[{idx}].kind: expected {kind!r}, got {actual!r}"


@then('catalyst {idx:d} has occurs_on "{date}"')
def step_occurs_on(context, idx: int, date: str) -> None:
    actual = context.catalysts[idx].occurs_on
    assert actual == date, f"catalyst[{idx}].occurs_on: expected {date!r}, got {actual!r}"


@then("catalyst {idx:d} has confidence at least {threshold:f}")
def step_confidence(context, idx: int, threshold: float) -> None:
    actual = context.catalysts[idx].confidence
    assert actual >= threshold, (
        f"catalyst[{idx}].confidence: expected ≥{threshold}, got {actual}"
    )
