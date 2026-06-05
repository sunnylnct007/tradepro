"""Steps for catalysts_fetcher.feature — exercises the Phase C-3.2
HTTP-backed CatalystFetcher with an injected fake HTTP. Never
touches the network."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from behave import given, then, when

from tradepro_strategies.paper.catalysts_fetcher import CatalystFetcher


# ── Fake HTTP transport ─────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no payload")
        return self._payload


class _FakeHttp:
    """Counts every call so scenarios can assert the cache stopped
    a duplicate hit. ``mode`` decides the response: 'ok' returns 200
    + ``payload``, '500' returns server error, 'raise' raises so
    fetch's try/except path runs."""

    def __init__(self, mode: str, payload: dict | None = None):
        self.mode = mode
        self.payload = payload or {"catalysts": []}
        self.calls = 0

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls += 1
        if self.mode == "ok":
            return _FakeResponse(200, self.payload)
        if self.mode == "500":
            return _FakeResponse(500, {})
        if self.mode == "raise":
            raise ConnectionError("boom")
        raise AssertionError(f"unknown mode {self.mode!r}")


def _row_from_table(row) -> dict:
    """Convert a feature-table row into the registry's JSON shape.
    ``occurs_on_offset`` is days from today (blank ⇒ undated)."""
    offset_raw = row.get("occurs_on_offset", "").strip()
    if offset_raw:
        d = (datetime.now(timezone.utc).date()
             + timedelta(days=int(offset_raw)))
        occurs_on = d.isoformat()
    else:
        occurs_on = None
    return {
        "id": int(row["id"]),
        "kind": row.get("kind", ""),
        "severity": row.get("severity", "low"),
        "occurs_on": occurs_on,
        "title": row.get("title", "test"),
        "source": row.get("source", "extractor.news"),
    }


# ── Givens ───────────────────────────────────────────────────────


@given('a fetcher with a fake HTTP that returns 200 with catalysts')
def step_fake_with_catalysts(context):
    payload = {"catalysts": [_row_from_table(r) for r in context.table]}
    context.fake_http = _FakeHttp("ok", payload)
    context.fetcher = CatalystFetcher(
        api_base="http://fake.example",
        token="tok",
        _http_fn=context.fake_http,
    )


@given('a fetcher with a fake HTTP that returns 200 with {n:d} catalysts')
def step_fake_with_n_catalysts(context, n):
    payload = {"catalysts": []}
    context.fake_http = _FakeHttp("ok", payload)
    context.fetcher = CatalystFetcher(
        api_base="http://fake.example",
        token="tok",
        _http_fn=context.fake_http,
    )


@given('a fetcher with a fake HTTP that returns {status:d}')
def step_fake_with_status(context, status):
    context.fake_http = _FakeHttp(str(status))
    context.fetcher = CatalystFetcher(
        api_base="http://fake.example",
        _http_fn=context.fake_http,
    )


@given('a fetcher with a fake HTTP that raises ConnectionError')
def step_fake_raising(context):
    context.fake_http = _FakeHttp("raise")
    context.fetcher = CatalystFetcher(
        api_base="http://fake.example",
        _http_fn=context.fake_http,
    )


# ── Whens ────────────────────────────────────────────────────────


@when('fetch runs for symbol ""')
def step_run_fetch_blank(context):
    context.last_hints = context.fetcher.fetch("")


@when('fetch runs for symbol "{symbol}"')
def step_run_fetch(context, symbol):
    context.last_hints = context.fetcher.fetch(symbol)


@when('fetch runs for symbol "{symbol}" again')
def step_run_fetch_again(context, symbol):
    context.last_hints = context.fetcher.fetch(symbol)


@when('invalidate runs for symbol "{symbol}"')
def step_invalidate(context, symbol):
    context.fetcher.invalidate(symbol)


# ── Thens ────────────────────────────────────────────────────────


@then('{n:d} hints are returned')
def step_assert_count(context, n):
    got = len(context.last_hints)
    assert got == n, f"expected {n} hints, got {got}"


@then('the hint with id {cid:d} has severity "{severity}"')
def step_assert_severity(context, cid, severity):
    match = next((h for h in context.last_hints if h.id == cid), None)
    assert match is not None, f"id {cid} not in {[h.id for h in context.last_hints]}"
    assert match.severity == severity, (
        f"id {cid}: expected severity {severity!r}, got {match.severity!r}"
    )


@then('the hint with id {cid:d} has days_until {value:d}')
def step_assert_days_until(context, cid, value):
    match = next((h for h in context.last_hints if h.id == cid), None)
    assert match is not None
    assert match.days_until == value, (
        f"id {cid}: expected days_until {value}, got {match.days_until}"
    )


@then('the hint with id {cid:d} has days_until None')
def step_assert_days_until_none(context, cid):
    match = next((h for h in context.last_hints if h.id == cid), None)
    assert match is not None
    assert match.days_until is None, (
        f"id {cid}: expected days_until None, got {match.days_until}"
    )


@then('the fake HTTP was called {n:d} time')
@then('the fake HTTP was called {n:d} times')
def step_assert_http_calls(context, n):
    assert context.fake_http.calls == n, (
        f"expected {n} HTTP calls, got {context.fake_http.calls}"
    )
