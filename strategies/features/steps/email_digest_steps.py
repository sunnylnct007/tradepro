"""Steps for email_digest.feature — exercise the pure builder."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.email_digest import build_digest


def _row(symbol: str, bucket: str, **extra) -> dict:
    base = {
        "symbol": symbol,
        "strategy": "buy_and_hold",
        "rank": 1,
        "in_position": True,
        "bucket": bucket,
        "bucket_reason": f"test reason for {symbol}",
        "stats": {
            "cagr_pct": 9.5,
            "sharpe": 0.71,
            "max_drawdown_pct": -22.3,
            "max_drawdown_recovery_days": 480,
            "max_drawdown_still_recovering": False,
        },
        "market_state": {
            "rsi_14": 55.0,
            "pct_off_52w_high_pct": 11.5,
            "drawdown_from_peak_pct": -11.5,
        },
    }
    base.update(extra)
    return base


def _envelope(universe: str, rows: list[dict]) -> dict:
    return {"universe": universe, "payload": {"universe": universe, "rows": rows}}


@given('a compare payload with one BUY symbol "{symbol}" in universe "{universe}"')
def step_one_buy(context, symbol: str, universe: str):
    context.payloads = [_envelope(universe, [_row(symbol, "BUY")])]


@given('a compare payload with 1 AVOID "{avoid}" and 2 WAIT "{wait_csv}" symbols')
def step_avoid_wait(context, avoid: str, wait_csv: str):
    waits = [s.strip() for s in wait_csv.split(",") if s.strip()]
    rows = [_row(avoid, "AVOID")] + [_row(s, "WAIT") for s in waits]
    context.payloads = [_envelope("etf_us_core", rows)]


@given("a compare payload with one BUY symbol that fully recovered from drawdown")
def step_recovered(context):
    row = _row("RECOVR", "BUY")
    row["stats"]["max_drawdown_recovery_days"] = 480
    row["stats"]["max_drawdown_still_recovering"] = False
    context.payloads = [_envelope("etf_us_core", [row])]


@given("a compare payload with one BUY symbol still in drawdown")
def step_still_down(context):
    row = _row("STILL", "BUY")
    row["stats"]["max_drawdown_recovery_days"] = None
    row["stats"]["max_drawdown_still_recovering"] = True
    context.payloads = [_envelope("etf_us_core", [row])]


@given("an empty list of compare payloads")
def step_empty(context):
    context.payloads = []


@given("a compare payload whose latest bar is 2 days old")
def step_stale(context):
    from datetime import datetime, timedelta, timezone
    stale_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    row = _row("STALE", "BUY")
    row["market_state"]["as_of"] = stale_date
    context.payloads = [_envelope("etf_uk_core", [row])]


@then('the html body contains "{snippet}"')
def step_html_contains(context, snippet: str):
    assert snippet in context.digest.html_body, (
        f"html body missing {snippet!r}"
    )


@when("I build the email digest")
def step_build(context):
    context.digest = build_digest(context.payloads)


@then('the subject mentions "{snippet}"')
def step_subject(context, snippet: str):
    assert snippet in context.digest.subject, (
        f"subject missing {snippet!r}: {context.digest.subject!r}"
    )


@then('the text body contains "{snippet}"')
def step_text_contains(context, snippet: str):
    assert snippet in context.digest.text_body, (
        f"text body missing {snippet!r}"
    )


@then("the html body has a BUY heading")
def step_html_buy_heading(context):
    assert "BUY candidates" in context.digest.html_body


@then("the html body has a row with the symbol")
def step_html_symbol_row(context):
    # We only put one symbol in this scenario; assert it shows.
    assert "VUKE.L" in context.digest.html_body


@then('the text body has BUY block marked "(none today)"')
def step_no_buy(context):
    body = context.digest.text_body
    # The BUY heading appears with (none today) underneath.
    assert "BUY candidates" in body
    assert "(none today)" in body


@then('the text body shows "(recovered" with day count')
def step_text_recovered(context):
    body = context.digest.text_body
    assert "(recovered" in body, body[:300]
    assert "d)" in body, body[:300]


@then('the text body shows "(still recovering)"')
def step_text_still(context):
    assert "(still recovering)" in context.digest.text_body
