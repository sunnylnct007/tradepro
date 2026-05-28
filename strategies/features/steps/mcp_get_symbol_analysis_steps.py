"""Steps for mcp_get_symbol_analysis.feature — stub the API fetch so
the wrapper's universe-fold behaviour is testable without spinning up
the .NET service or yfinance."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.mcp import tools as mcp_tools


def _fake_row(symbol: str, *, strategy: str, sharpe: float,
              bucket: str, conviction: str) -> dict:
    return {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "bucket": bucket,
        "bucket_reason": "fake",
        "conviction": conviction,
        "conviction_reason": "fake",
        "stats": {"sharpe": sharpe},
        "rr_gate": {"passed": True, "rr": 2.5, "reason": "fake"},
    }


def _install_fake_fetch(context, rows: list[dict]) -> None:
    context.original_get = mcp_tools._get

    def fake_get(path, params=None):
        return {
            "universe": (params or {}).get("universe"),
            "payload": {"rows": rows},
        }

    mcp_tools._get = fake_get  # type: ignore[assignment]
    context.add_cleanup(lambda: setattr(mcp_tools, "_get", context.original_get))


@given('a fake compare API response with rows for "{symbol}"')
def step_fake_rows(context, symbol: str) -> None:
    rows = [
        _fake_row(
            symbol,
            strategy=row["strategy"],
            sharpe=float(row["sharpe"]),
            bucket=row["bucket"],
            conviction=row["conviction"],
        )
        for row in context.table
    ]
    _install_fake_fetch(context, rows)


@when('I call tools.get_symbol_analysis for "{symbol}" with no universe')
def step_call_no_universe(context, symbol: str) -> None:
    context.sa_response = mcp_tools.get_symbol_analysis(symbol)


@when('I call tools.get_symbol_analysis for "{symbol}" with universe "{universe}"')
def step_call_with_universe(context, symbol: str, universe: str) -> None:
    context.sa_response = mcp_tools.get_symbol_analysis(symbol, universe=universe)


@when('I call tools.get_symbol_analysis with an empty symbol')
def step_call_empty(context) -> None:
    context.sa_response = mcp_tools.get_symbol_analysis("")


@then('the response is ok')
def step_ok(context) -> None:
    assert context.sa_response.get("ok") is True, context.sa_response


@then('the response is not ok')
def step_not_ok(context) -> None:
    assert context.sa_response.get("ok") is not True, context.sa_response


@then('the response _source is "{expected}"')
def step_source(context, expected: str) -> None:
    actual = context.sa_response.get("_source")
    assert actual == expected, f"_source: expected {expected!r}, got {actual!r}"


@then('the response _source starts with "{prefix}"')
def step_source_prefix(context, prefix: str) -> None:
    actual = context.sa_response.get("_source") or ""
    assert actual.startswith(prefix), (
        f"_source: expected to start with {prefix!r}, got {actual!r}"
    )


@then('the response has primary_horizon_recommendation')
def step_has_horizon(context) -> None:
    assert context.sa_response.get("primary_horizon_recommendation"), (
        f"missing primary_horizon_recommendation: {context.sa_response}"
    )


@then('the response payload technical block is null')
def step_tech_null(context) -> None:
    tech = context.sa_response.get("technical")
    assert tech is None, f"expected technical=None, got {tech!r}"


@then('the response payload has a fundamental block')
def step_has_fund(context) -> None:
    fund = context.sa_response.get("fundamental")
    assert fund is not None and "quality_snapshot" in fund, (
        f"fundamental block malformed: {fund}"
    )


@then('the response compare_row_source is null')
def step_crs_null(context) -> None:
    assert context.sa_response.get("compare_row_source") is None, (
        f"expected compare_row_source=None, got {context.sa_response.get('compare_row_source')!r}"
    )


@then('the response compare_row_source is "{expected}"')
def step_crs(context, expected: str) -> None:
    actual = context.sa_response.get("compare_row_source")
    assert actual == expected, (
        f"compare_row_source: expected {expected!r}, got {actual!r}"
    )


@then('the response payload technical bucket is "{expected}"')
def step_tech_bucket(context, expected: str) -> None:
    tech = context.sa_response.get("technical") or {}
    assert tech.get("bucket") == expected, (
        f"technical.bucket: expected {expected!r}, got {tech.get('bucket')!r}"
    )


@then('the response payload technical conviction is "{expected}"')
def step_tech_conviction(context, expected: str) -> None:
    tech = context.sa_response.get("technical") or {}
    assert tech.get("conviction") == expected, (
        f"technical.conviction: expected {expected!r}, got {tech.get('conviction')!r}"
    )
