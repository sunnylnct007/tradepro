"""Steps for catalysts_sink.feature — exercises the Phase C-2 sink
without hitting the .NET endpoint. Real `requests.post` calls are
monkey-patched with a recording fake so the BDD suite is offline."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

from behave import given, then, when

from tradepro_strategies.catalysts import Catalyst
from tradepro_strategies.catalysts_sink import (
    catalyst_to_upsert_body,
    extract_and_post,
    severity_from_confidence,
)


# ── Severity heuristic ─────────────────────────────────────────────


@given('a catalyst with confidence {conf:f}')
def step_catalyst_with_confidence(context, conf):
    context.catalyst_confidence = conf


@when('severity_from_confidence runs')
def step_run_severity(context):
    context.severity = severity_from_confidence(context.catalyst_confidence)


@then('the severity is "{expected}"')
def step_assert_severity(context, expected):
    assert context.severity == expected, (
        f"confidence={context.catalyst_confidence} → got {context.severity!r}, "
        f"expected {expected!r}"
    )


# ── Body shape ─────────────────────────────────────────────────────


@given('a catalyst')
def step_catalyst_from_table(context):
    fields = {row["field"]: row["value"] for row in context.table}
    context.catalyst = Catalyst(
        kind=fields["kind"],
        title=fields["title"],
        occurs_on=fields.get("occurs_on") or None,
        surfaced_at=fields.get("surfaced_at") or None,
        confidence=float(fields["confidence"]),
        link=fields.get("link"),
        rationale=fields.get("rationale", ""),
    )


@when('the body is built for symbol "{symbol}"')
def step_build_body(context, symbol):
    context.body = catalyst_to_upsert_body(context.catalyst, symbol=symbol)


@then('the body field "{field}" equals "{expected}"')
def step_assert_body_field(context, field, expected):
    got = context.body.get(field)
    assert str(got) == expected, (
        f"body[{field!r}] = {got!r}, expected {expected!r}"
    )


@then('the body payload nested {field} equals {expected:f}')
def step_assert_payload_numeric(context, field, expected):
    got = context.body["Payload"].get(field)
    assert got == expected, (
        f"Payload[{field!r}] = {got!r}, expected {expected}"
    )


@then('the body payload nested {field} equals "{expected}"')
def step_assert_payload_string(context, field, expected):
    got = context.body["Payload"].get(field)
    assert got == expected, (
        f"Payload[{field!r}] = {got!r}, expected {expected!r}"
    )


# ── End-to-end extract_and_post with a recording fake ──────────────


class _RecordingResponse:
    """Stand-in for requests.Response. `ok` and `status_code` are the
    only attributes the sink touches."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = "(fake)"


def _make_fake_post(status_code: int, calls: list[dict[str, Any]]):
    def fake(url, json=None, headers=None, timeout=None):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers})
        return _RecordingResponse(status_code)
    return fake


@given('an empty news list for symbol "{symbol}"')
def step_empty_news(context, symbol):
    context.target_symbol = symbol
    context.news_items = []


@given('a news list with 2 dated election headlines for symbol "{symbol}"')
def step_two_election_headlines(context, symbol):
    context.target_symbol = symbol
    # Two headlines that the extractor will pick up as election with
    # distinct dates so they don't dedupe.
    context.news_items = [
        {
            "title": "Colombia presidential election in 10 days",
            "published_at": "2026-06-03T12:00:00Z",
        },
        {
            "title": "Brazil runoff vote on October 15",
            "published_at": "2026-09-20T12:00:00Z",
        },
    ]


def _run_with_fake(context, status_code: int):
    calls: list[dict[str, Any]] = []
    fake = _make_fake_post(status_code, calls)
    with patch("tradepro_strategies.catalysts_sink.requests.post", side_effect=fake):
        context.report = extract_and_post(
            symbol=context.target_symbol,
            news_items=context.news_items,
            api_base="http://fake",
            token="fake-token",
        )
    context.post_calls = calls


@when('extract_and_post runs with a recording fake')
def step_run_recording(context):
    _run_with_fake(context, status_code=200)


@when('extract_and_post runs with a recording fake that fails every POST')
def step_run_failing(context):
    _run_with_fake(context, status_code=500)


@then('{n:d} POSTs are recorded')
def step_assert_post_count(context, n):
    assert len(context.post_calls) == n, (
        f"expected {n} POSTs, got {len(context.post_calls)}"
    )


@then('the summary reports catalysts_extracted = {n:d}')
def step_assert_extracted(context, n):
    got = context.report["catalysts_extracted"]
    assert got == n, f"catalysts_extracted = {got}, expected {n}"


@then('the summary reports catalysts_posted = {n:d}')
def step_assert_posted(context, n):
    got = context.report["catalysts_posted"]
    assert got == n, f"catalysts_posted = {got}, expected {n}"


@then('catalysts_extracted equals catalysts_failed')
def step_assert_all_failed(context):
    e = context.report["catalysts_extracted"]
    f = context.report["catalysts_failed"]
    assert e == f and e > 0, (
        f"expected catalysts_extracted == catalysts_failed > 0, "
        f"got extracted={e} failed={f}"
    )


@then('catalysts_posted is 0')
def step_assert_zero_posted(context):
    assert context.report["catalysts_posted"] == 0, (
        f"expected 0 posted, got {context.report['catalysts_posted']}"
    )


@then('no exception bubbles up')
def step_no_exception(context):
    # If we got here, the When step returned without raising.
    assert context.report is not None
