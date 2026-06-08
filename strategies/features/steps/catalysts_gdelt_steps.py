"""Steps for catalysts_gdelt.feature — exercises the GDELT macro/event
source with the HTTP layer mocked. No live network in any scenario.

We drive ``fetch_all_catalysts`` with a single curated MacroQuery per
scenario (chosen so the proxy symbol is deterministic) and monkeypatch
``requests.get`` inside the gdelt module with a recording fake."""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import requests
from behave import given, then, when

from tradepro_strategies import catalysts_gdelt
from tradepro_strategies.catalysts_gdelt import (
    MacroQuery,
    fetch_all_catalysts,
)
from tradepro_strategies.catalysts_sink import catalyst_to_upsert_body


# Map a proxy symbol → a single representative query, so a scenario that
# asserts on "SPY" or "XLE" runs exactly one GDELT call for that symbol.
_QUERY_FOR_SYMBOL = {
    "SPY": MacroQuery(
        name="fomc_rate_decision", query="(FOMC)", symbol="SPY",
        kind="macro", severity="high",
    ),
    "XLE": MacroQuery(
        name="opec_oil", query="(OPEC)", symbol="XLE",
        kind="macro", severity="high",
    ),
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, *, text: str = "",
                 json_raises: bool = False):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not JSON")
        return self._payload


def _install_fake(context, fake_get):
    """Patch requests.get for the duration of the When step and run all
    queries that the scenario set up (defaults to SPY)."""
    context._fake_get = fake_get


# ── Given: shape the fake GDELT ────────────────────────────────────────


@given("a fake GDELT that returns articles")
def step_fake_articles(context):
    articles = []
    for row in context.table:
        articles.append({
            "title": row["title"],
            "seendate": row["seendate"],
            "url": row["url"],
            "domain": row["domain"],
        })
    payload = {"articles": articles}

    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(200, payload)

    _install_fake(context, fake_get)


@given("a fake GDELT that raises ConnectionError")
def step_fake_raises(context):
    def fake_get(url, params=None, headers=None, timeout=None):
        raise requests.exceptions.ConnectionError("boom")

    _install_fake(context, fake_get)


@given("a fake GDELT that returns status {code:d}")
def step_fake_status(context, code):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(code, None, text="upstream error")

    _install_fake(context, fake_get)


@given("a fake GDELT that returns a non-JSON body")
def step_fake_non_json(context):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResponse(200, None, text="<html>oops</html>", json_raises=True)

    _install_fake(context, fake_get)


# ── When: run the fetch ────────────────────────────────────────────────


@when("GDELT catalysts are fetched")
def step_fetch(context):
    # Run BOTH curated proxy queries so a scenario can assert on whichever
    # proxy symbol it cares about; the fake returns the same payload for
    # each call but the dedupe keeps it to one catalyst per symbol/theme.
    queries = tuple(_QUERY_FOR_SYMBOL.values())
    context.gdelt_error = None
    try:
        with patch.object(catalysts_gdelt.requests, "get", side_effect=context._fake_get):
            context.reports = fetch_all_catalysts(queries=queries, inter_query_delay=0)
    except Exception as exc:  # noqa: BLE001 — scenarios assert this never happens
        context.gdelt_error = exc
        context.reports = {}


def _catalysts_for(context, symbol):
    rep = context.reports.get(symbol)
    return list(rep.all_catalysts) if rep else []


# ── Then: assertions ───────────────────────────────────────────────────


@then('the symbol "{symbol}" has {n:d} catalyst')
@then('the symbol "{symbol}" has {n:d} catalysts')
def step_symbol_count(context, symbol, n):
    cats = _catalysts_for(context, symbol)
    assert len(cats) == n, (
        f"expected {n} catalyst(s) on {symbol}, got {len(cats)}: "
        f"{[c.title for c in cats]}"
    )


@then('the {symbol} catalyst kind is "{kind}"')
def step_catalyst_kind(context, symbol, kind):
    cats = _catalysts_for(context, symbol)
    assert cats and cats[0].kind == kind, (
        f"{symbol} catalyst kind = {cats[0].kind if cats else None!r}, expected {kind!r}"
    )


@then('the {symbol} catalyst occurs_on is "{date}"')
def step_catalyst_occurs_on(context, symbol, date):
    cats = _catalysts_for(context, symbol)
    assert cats and cats[0].occurs_on == date, (
        f"{symbol} occurs_on = {cats[0].occurs_on if cats else None!r}, expected {date!r}"
    )


@then("the {symbol} catalyst occurs_on is None")
def step_catalyst_occurs_on_none(context, symbol):
    cats = _catalysts_for(context, symbol)
    assert cats and cats[0].occurs_on is None, (
        f"{symbol} occurs_on = {cats[0].occurs_on if cats else None!r}, expected None"
    )


@then("the {symbol} catalyst confidence is {conf:f}")
def step_catalyst_confidence(context, symbol, conf):
    cats = _catalysts_for(context, symbol)
    assert cats and abs(cats[0].confidence - conf) < 1e-9, (
        f"{symbol} confidence = {cats[0].confidence if cats else None!r}, expected {conf}"
    )


@then('the {symbol} catalyst source-tagged body uses source "{source}"')
def step_catalyst_source(context, symbol, source):
    cats = _catalysts_for(context, symbol)
    assert cats, f"no catalyst on {symbol} to build a body for"
    body = catalyst_to_upsert_body(cats[0], symbol=symbol, source=source)
    assert body["Source"] == source, (
        f"body Source = {body['Source']!r}, expected {source!r}"
    )
    assert body["Symbol"] == symbol
    assert body["Kind"] == cats[0].kind


@then("no symbols have catalysts")
def step_no_catalysts(context):
    total = sum(len(rep.all_catalysts) for rep in context.reports.values())
    assert total == 0, f"expected 0 catalysts across all symbols, got {total}"


@then("no exception bubbles up from GDELT")
def step_no_exception(context):
    assert context.gdelt_error is None, (
        f"GDELT raised instead of failing open: {context.gdelt_error!r}"
    )
