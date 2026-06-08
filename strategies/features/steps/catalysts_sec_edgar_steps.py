"""Steps for catalysts_sec_edgar.feature — exercises the SEC EDGAR filing
source with the HTTP layer fully mocked. No live network in any scenario.

The fake ``requests.get`` routes by URL: the ticker-map URL returns the
company_tickers.json shape; the submissions URL returns a filings.recent
block built from the scenario table (or an error, per the scenario)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import patch

import requests
from behave import given, then, when

from tradepro_strategies import catalysts_sec_edgar
from tradepro_strategies.catalysts_sec_edgar import (
    fetch_all_catalysts,
    reset_cik_cache,
    resolve_cik,
)

# Anchor "now" so window assertions are deterministic regardless of the
# real clock (the feature dates cluster around early June 2026).
_NOW = datetime(2026, 6, 7, tzinfo=timezone.utc)


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


def _ticker_payload(context) -> dict:
    """company_tickers.json shape: index-keyed dict of {cik_str, ticker, title}."""
    rows = {}
    for i, (ticker, cik) in enumerate(context.ticker_map.items()):
        rows[str(i)] = {"cik_str": int(cik), "ticker": ticker, "title": f"{ticker} Inc."}
    return rows


def _submissions_payload(filings_rows: list[dict]) -> dict:
    """Build the submissions API filings.recent parallel-array block."""
    return {
        "filings": {
            "recent": {
                "form": [r.get("form", "") for r in filings_rows],
                "filingDate": [r.get("filingDate", "") for r in filings_rows],
                "items": [r.get("items", "") for r in filings_rows],
                "accessionNumber": [r.get("accessionNumber", "") for r in filings_rows],
                "primaryDocument": [r.get("primaryDocument", "") for r in filings_rows],
            }
        }
    }


def _make_fake_get(context):
    """Return a fake requests.get routing ticker-map vs submissions URLs."""
    ticker_payload = _ticker_payload(context)
    sub_payload = getattr(context, "submissions_payload", None)
    sub_mode = getattr(context, "submissions_mode", "ok")

    def fake_get(url, params=None, headers=None, timeout=None):
        if "company_tickers.json" in url:
            return _FakeResponse(200, ticker_payload)
        # submissions URL
        if sub_mode == "raise":
            raise requests.exceptions.ConnectionError("boom")
        if sub_mode == "status":
            return _FakeResponse(context.submissions_status, None, text="forbidden")
        if sub_mode == "non_json":
            return _FakeResponse(200, None, text="<html>oops</html>", json_raises=True)
        return _FakeResponse(200, sub_payload)

    return fake_get


# ── Given: shape the fakes ──────────────────────────────────────────────


@given("the SEC CIK cache is reset")
def step_reset_cache(context):
    reset_cik_cache()


@given("a fake SEC ticker map")
def step_ticker_map(context):
    context.ticker_map = {row["ticker"]: row["cik"] for row in context.table}


# ── When: resolution + fetch ────────────────────────────────────────────


@when('the CIK for "{symbol}" is resolved')
def step_resolve_cik(context, symbol):
    with patch.object(catalysts_sec_edgar.requests, "get", side_effect=_make_fake_get(context)):
        context.resolved_cik = resolve_cik(symbol)


def _run_fetch(context, symbol, *, days_back=30, filings_rows=None):
    context.submissions_payload = _submissions_payload(filings_rows or [])
    context.submissions_mode = getattr(context, "submissions_mode", "ok")
    context.sec_error = None
    try:
        with patch.object(catalysts_sec_edgar.requests, "get",
                          side_effect=_make_fake_get(context)):
            context.reports = fetch_all_catalysts(
                [symbol], days_back=days_back, inter_symbol_delay=0, now=_NOW,
            )
    except Exception as exc:  # noqa: BLE001 — scenarios assert this never happens
        context.sec_error = exc
        context.reports = {}


@when('SEC catalysts are fetched for "{symbol}" with filings')
def step_fetch_with_filings(context, symbol):
    rows = [dict(r.as_dict()) for r in context.table]
    _run_fetch(context, symbol, filings_rows=rows)


@when('SEC catalysts are fetched for "{symbol}" within {days:d} days with filings')
def step_fetch_window(context, symbol, days):
    rows = [dict(r.as_dict()) for r in context.table]
    _run_fetch(context, symbol, days_back=days, filings_rows=rows)


@when('SEC catalysts are fetched for "{symbol}" but submissions returns status {code:d}')
def step_fetch_status(context, symbol, code):
    context.submissions_mode = "status"
    context.submissions_status = code
    _run_fetch(context, symbol, filings_rows=[])


@when('SEC catalysts are fetched for "{symbol}" but the network raises ConnectionError')
def step_fetch_raise(context, symbol):
    context.submissions_mode = "raise"
    _run_fetch(context, symbol, filings_rows=[])


@when('SEC catalysts are fetched for "{symbol}" but submissions returns a non-JSON body')
def step_fetch_non_json(context, symbol):
    context.submissions_mode = "non_json"
    _run_fetch(context, symbol, filings_rows=[])


# ── Then: assertions ────────────────────────────────────────────────────


def _report_for(context, symbol):
    return context.reports.get(symbol)


def _catalysts_for(context, symbol):
    rep = _report_for(context, symbol)
    return list(rep.all_catalysts) if rep else []


@then('the resolved CIK is "{cik}"')
def step_resolved_cik(context, cik):
    assert context.resolved_cik == cik, (
        f"resolved CIK = {context.resolved_cik!r}, expected {cik!r}"
    )


@then('the symbol "{symbol}" is skipped')
def step_skipped(context, symbol):
    rep = _report_for(context, symbol)
    assert rep is not None and rep.skipped, (
        f"{symbol} report skipped = {getattr(rep, 'skipped', None)!r}, expected True"
    )


# NOTE: the count / kind / occurs_on / confidence / source-tagged-body
# Then-steps are shared with catalysts_gdelt_steps.py — both store
# ``context.reports`` keyed by symbol with ``.all_catalysts``, so behave's
# global step registry reuses the GDELT definitions. Only the SEC-unique
# steps below are defined here to avoid AmbiguousStep collisions.


@then('the {symbol} catalyst title contains "{frag}"')
def step_catalyst_title_contains(context, symbol, frag):
    cats = _catalysts_for(context, symbol)
    assert cats and frag in cats[0].title, (
        f"{symbol} title = {cats[0].title if cats else None!r}, expected to contain {frag!r}"
    )


@then('the {symbol} catalyst link contains "{frag}"')
def step_catalyst_link_contains(context, symbol, frag):
    cats = _catalysts_for(context, symbol)
    assert cats and cats[0].link and frag in cats[0].link, (
        f"{symbol} link = {cats[0].link if cats else None!r}, expected to contain {frag!r}"
    )


@then("no exception bubbles up from SEC")
def step_no_exception(context):
    assert context.sec_error is None, (
        f"SEC raised instead of failing open: {context.sec_error!r}"
    )
