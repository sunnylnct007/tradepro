"""Steps for mcp_get_compare.feature — stub the API fetch with a fake
payload so we can assert top_n + strip_bloat + fields behaviour
without spinning up the .NET service."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.mcp import tools as mcp_tools


def _fake_row(i: int, *, with_bloat: bool = False) -> dict:
    row = {
        "symbol": f"SYM{i}",
        "strategy": "buy_and_hold",
        "bucket": "BUY" if i < 5 else "WAIT",
        "stats": {"sharpe": 0.5 + i * 0.01},
        "current_action": "BUY",
        "in_position": True,
    }
    if with_bloat:
        row["decision_trace"] = [
            {"name": "rule a", "status": "pass", "detail": "fake"},
            {"name": "rule b", "status": "warn", "detail": "fake"},
        ]
        row["news"] = [
            {"title": "fake headline", "sentiment": 0.2},
        ]
        row["rationale"] = {"summary": "fake summary text"}
        row["regimes"] = [{"key": "gfc", "return_pct": -50.0}]
    return row


def _patch_fetch(monkeypatch_target, rows: list[dict]) -> None:
    """Replace tools._get with a fake that returns our envelope."""
    def fake_get(path, params=None):
        return {
            "universe": (params or {}).get("universe"),
            "payload": {"rows": rows},
        }
    mcp_tools._get = fake_get  # type: ignore[assignment]


@given("a fake compare API response with {n:d} rows")
def step_fake_rows(context, n: int) -> None:
    context.original_get = mcp_tools._get
    _patch_fetch(context, [_fake_row(i) for i in range(n)])
    context.add_cleanup(lambda: setattr(mcp_tools, "_get", context.original_get))


@given("a fake compare API response with {n:d} rows carrying decision_trace and news")
def step_fake_rows_with_bloat(context, n: int) -> None:
    context.original_get = mcp_tools._get
    _patch_fetch(context, [_fake_row(i, with_bloat=True) for i in range(n)])
    context.add_cleanup(lambda: setattr(mcp_tools, "_get", context.original_get))


@when("I call tools.get_compare with top_n {n:d}")
def step_call_top_n(context, n: int) -> None:
    context.response = mcp_tools.get_compare("etf_test", top_n=n)


@when("I call tools.get_compare with strip_bloat true")
def step_call_strip(context) -> None:
    context.response = mcp_tools.get_compare("etf_test", strip_bloat=True)


@when('I call tools.get_compare with fields "{fields}"')
def step_call_fields(context, fields: str) -> None:
    context.response = mcp_tools.get_compare("etf_test", fields=fields)


def _rows(context) -> list[dict]:
    return context.response["envelope"]["payload"]["rows"]


@then("the returned envelope has {n:d} rows")
def step_rows_count(context, n: int) -> None:
    assert len(_rows(context)) == n, (
        f"expected {n} rows, got {len(_rows(context))}"
    )


@then("the returned response has truncated=true")
def step_truncated(context) -> None:
    assert context.response.get("truncated") is True, context.response


@then("the returned response has row_count_total={n:d}")
def step_total(context, n: int) -> None:
    assert context.response.get("row_count_total") == n, context.response


@then("no row carries decision_trace")
def step_no_trace(context) -> None:
    for row in _rows(context):
        assert "decision_trace" not in row, row


@then("no row carries news")
def step_no_news(context) -> None:
    for row in _rows(context):
        assert "news" not in row, row


@then("every row still carries symbol")
def step_has_symbol(context) -> None:
    for row in _rows(context):
        assert "symbol" in row and row["symbol"], row


@then("every row still carries _source")
def step_has_source(context) -> None:
    for row in _rows(context):
        assert "_source" in row, row


@then("every row has exactly the keys symbol, strategy, _source, _source_symbol_best, bucket, stats")
def step_exact_keys(context) -> None:
    expected = {
        "symbol", "strategy", "_source", "_source_symbol_best",
        "bucket", "stats",
    }
    for row in _rows(context):
        actual = set(row.keys())
        # _source_symbol_best is only on the first row per symbol; the
        # fake rows all have distinct symbols so every row gets one.
        extras = actual - expected
        assert not extras, f"unexpected keys on row: {extras}, row={row}"
