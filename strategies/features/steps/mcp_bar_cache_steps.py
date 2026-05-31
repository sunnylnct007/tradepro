"""Steps for mcp_bar_cache.feature.

Same stubbing pattern as mcp_get_symbol_analysis: monkey-patch
tradepro_strategies.mcp.tools._get with a fake responder so the
MCP tool wrappers can be exercised without hitting the .NET API
or yfinance.

The get_bars scenarios additionally swap the YFinanceProvider's
fetcher via the provider registry so the BarStore's cache-miss
path hits the synthetic frame.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from behave import given, then, when

from tradepro_strategies.mcp import tools as mcp_tools


# ─── Generic fake API ────────────────────────────────────────────


def _install_fake_api(context, responder) -> None:
    """Swap mcp_tools._get with a function that returns whatever
    ``responder(path, params)`` produces. Restored via behave's
    cleanup hook so a later scenario sees the real _get."""
    context._original_get = mcp_tools._get

    def fake_get(path, params=None):
        return responder(path, params or {})

    mcp_tools._get = fake_get  # type: ignore[assignment]
    context.add_cleanup(
        lambda: setattr(mcp_tools, "_get", context._original_get)
    )


# ─── Pure-local tool scenarios (no fake API needed) ───────────────


@when("I call tools.bar_cache_list_asset_classes")
def step_list_asset_classes(context) -> None:
    context.sa_response = mcp_tools.bar_cache_list_asset_classes()


@when("I call tools.bar_cache_list_providers")
def step_list_providers(context) -> None:
    context.sa_response = mcp_tools.bar_cache_list_providers()


@then('the response asset_classes includes a plugin named "{name}"')
def step_asset_class_present(context, name: str) -> None:
    names = [a["name"] for a in context.sa_response.get("asset_classes", [])]
    assert name in names, f"{name!r} not in {names}"


@then('the {ac} plugin schema_version is "{ver}"')
def step_asset_class_schema_version(context, ac: str, ver: str) -> None:
    rows = [a for a in context.sa_response["asset_classes"] if a["name"] == ac]
    assert rows, f"no plugin {ac!r}"
    assert rows[0]["schema_version"] == ver, rows[0]["schema_version"]


@then('the response providers includes a provider named "{name}"')
def step_provider_present(context, name: str) -> None:
    names = [p["name"] for p in context.sa_response.get("providers", [])]
    assert name in names, f"{name!r} not in {names}"


@then("the {provider} provider documents the {res} resolution")
def step_provider_supports_resolution(
    context, provider: str, res: str,
) -> None:
    rows = [p for p in context.sa_response["providers"] if p["name"] == provider]
    assert rows, f"no provider {provider!r}"
    resolutions = [r["resolution"] for r in rows[0]["resolutions"]]
    assert res in resolutions, f"{res!r} not in {resolutions}"


# ─── Health / events / preferences scenarios ─────────────────────


@given(
    'a fake bar-cache API returning a health row for "{sym}" in "{ac}"'
)
def step_fake_health(context, sym: str, ac: str) -> None:
    def responder(path: str, params: dict) -> dict:
        assert path.endswith("/bar-cache/health"), path
        return {
            "health": [
                {
                    "canonical": sym,
                    "asset_class": ac,
                    "last_fetched_at_utc": "2024-12-31T20:00:00Z",
                    "last_fetched_result": "complete",
                    "last_fetched_provider": "yfinance",
                    "last_fetched_resolution": "1d",
                    "coverage_start_date": "2024-01-02",
                    "coverage_end_date": "2024-12-31",
                    "coverage_partitions": 12,
                    "missing_days_count": 0,
                    "schema_version": "us_equity_v1",
                    "manifest_violations_last_30d": 0,
                    "last_corp_action_at_utc": None,
                    "last_corp_action_type": None,
                    "updated_at_utc": "2024-12-31T20:00:01Z",
                },
            ],
        }

    _install_fake_api(context, responder)


@when('I call tools.bar_cache_health filtered by canonical "{sym}"')
def step_call_health(context, sym: str) -> None:
    context.sa_response = mcp_tools.bar_cache_health(canonical=sym)


@given('a fake bar-cache API returning {n:d} fetch events for "{sym}"')
def step_fake_events(context, n: int, sym: str) -> None:
    rows = [
        {
            "id": i,
            "occurred_at_utc": "2024-12-31T20:00:00Z",
            "canonical": sym,
            "asset_class": "us_etf",
            "resolution": "1d",
            "range_start_utc": "2024-01-01T00:00:00Z",
            "range_end_utc": "2024-12-31T23:59:59Z",
            "result": "complete",
            "source_chain": ["cache_hit"],
            "provider_used": "cache",
            "provider_versions_text": "{}",
            "rows_expected": 252,
            "rows_returned": 252,
            "gaps_detected_count": 0,
            "schema_version": "us_equity_v1",
            "latency_ms": 14,
            "error_class": None,
            "error_provider": None,
            "error_message": None,
            "retry_strategy": None,
        }
        for i in range(n)
    ]

    def responder(path: str, params: dict) -> dict:
        assert path.endswith("/bar-cache/events"), path
        return {"events": rows}

    _install_fake_api(context, responder)


@when(
    'I call tools.bar_cache_events filtered by canonical "{sym}" '
    'with limit {limit:d}'
)
def step_call_events(context, sym: str, limit: int) -> None:
    context.sa_response = mcp_tools.bar_cache_events(canonical=sym, limit=limit)


@given(
    "a fake bar-cache API returning preferences for us_etf/1m and us_etf/1d"
)
def step_fake_preferences(context) -> None:
    def responder(path: str, params: dict) -> dict:
        assert path.endswith("/data-trust/preferences"), path
        return {
            "validProviders": ["yfinance", "ig", "finnhub"],
            "preferences": [
                {
                    "asset_class": "us_etf",
                    "resolution": "1m",
                    "provider_chain": ["yfinance"],
                    "notes": None,
                    "updated_at_utc": "2024-01-01T00:00:00Z",
                    "updated_by": "migration",
                },
                {
                    "asset_class": "us_etf",
                    "resolution": "1d",
                    "provider_chain": ["yfinance"],
                    "notes": None,
                    "updated_at_utc": "2024-01-01T00:00:00Z",
                    "updated_by": "migration",
                },
            ],
        }

    _install_fake_api(context, responder)


@when(
    'I call tools.bar_cache_provider_preferences for asset_class "{ac}" '
    'resolution "{res}"'
)
def step_call_preferences(context, ac: str, res: str) -> None:
    context.sa_response = mcp_tools.bar_cache_provider_preferences(
        asset_class=ac, resolution=res,
    )


# ─── Then assertions ─────────────────────────────────────────────
# 'the response is ok' / 'is not ok' / '_source is X' are already
# defined in mcp_get_symbol_analysis_steps.py against
# ``context.sa_response``. We reuse them here by stashing our tool
# responses on the same attribute name — keeps step registration
# unambiguous and the cross-test step library small.


@then("the response count is {n:d}")
def step_response_count(context, n: int) -> None:
    assert context.sa_response.get("count") == n, context.sa_response.get("count")


@then('the response rows[0] canonical is "{val}"')
def step_response_row0_canonical(context, val: str) -> None:
    assert context.sa_response["rows"][0]["canonical"] == val


@then('the response events[0] result is "{val}"')
def step_response_event0_result(context, val: str) -> None:
    assert context.sa_response["events"][0]["result"] == val


@then('the response preferences[0] resolution is "{val}"')
def step_response_pref0_resolution(context, val: str) -> None:
    assert context.sa_response["preferences"][0]["resolution"] == val


@then('the response error_class is "{val}"')
def step_response_error_class(context, val: str) -> None:
    assert context.sa_response.get("error_class") == val, (
        context.sa_response.get("error_class")
    )


@then('the response retry_strategy is "{val}"')
def step_response_retry_strategy(context, val: str) -> None:
    assert context.sa_response.get("retry_strategy") == val, (
        context.sa_response.get("retry_strategy")
    )


@then("the response summary coverage_complete is False")
def step_summary_coverage_false(context) -> None:
    assert context.sa_response["summary"]["coverage_complete"] is False, (
        context.sa_response["summary"]
    )


@then("the response summary rows_returned is {n:d}")
def step_summary_rows_returned(context, n: int) -> None:
    assert context.sa_response["summary"]["rows_returned"] == n, (
        context.sa_response["summary"]
    )


# ─── get_bars scenarios (synthetic provider + tmp cache) ─────────


@given(
    'a fake yfinance provider returning bars only for {y:d}-{m:d}-{d:d}'
)
def step_fake_yf_provider(context, y: int, m: int, d: int) -> None:
    """Re-register the yfinance provider with an injected fetch_fn
    that returns only one day of bars. The mcp tool builds its own
    BarStore on call, so we swap at the global registry."""
    from tradepro_strategies.bar_cache.providers.base import (
        register_provider,
    )
    from tradepro_strategies.bar_cache.providers.yfinance_provider import (
        YFinanceProvider,
    )

    target_date = date(y, m, d)

    def _fake(symbol, interval, start, end):
        idx = pd.date_range(
            f"{target_date} 14:30", f"{target_date} 21:00",
            freq="1min", tz="UTC", inclusive="left",
        )
        df = pd.DataFrame(
            {
                "Open": [100.0] * len(idx),
                "High": [101.0] * len(idx),
                "Low":  [99.0]  * len(idx),
                "Close": [100.5] * len(idx),
                "Volume": [1000] * len(idx),
            },
            index=idx,
        )
        # Filter to the requested window so the partition slicer
        # works correctly.
        return df[(df.index >= start) & (df.index < end)]

    register_provider(YFinanceProvider(_fetch_fn=_fake))


@given("a fresh bar cache base directory at the home location is unavailable")
def step_tmp_home(context) -> None:
    """Redirect the get_bars tool's Path.home() to a fresh tmpdir so
    the test doesn't pollute the real ~/.tradepro/bar_cache."""
    if not hasattr(context, "_tmp_home"):
        context._tmp_home = tempfile.mkdtemp(prefix="mcp_bar_cache_")

    import pathlib
    context._original_home = pathlib.Path.home
    pathlib.Path.home = staticmethod(lambda: pathlib.Path(context._tmp_home))  # type: ignore[assignment]
    context.add_cleanup(_restore_home, context)


def _restore_home(context):
    import pathlib
    pathlib.Path.home = context._original_home  # type: ignore[assignment]
    shutil.rmtree(context._tmp_home, ignore_errors=True)


@when(
    "I call tools.bar_cache_get_bars for SPY us_etf 1m "
    "{from_y:d}-{from_m:d}-{from_d:d} to "
    "{to_y:d}-{to_m:d}-{to_d:d} without allow_partial"
)
def step_call_get_bars_no_partial(
    context, from_y, from_m, from_d, to_y, to_m, to_d,
):
    _ensure_tmp_home(context)
    context.sa_response = mcp_tools.bar_cache_get_bars(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        from_date=f"{from_y:04d}-{from_m:02d}-{from_d:02d}",
        to_date=f"{to_y:04d}-{to_m:02d}-{to_d:02d}",
        allow_partial=False,
        summary_only=True,
    )


@when(
    "I call tools.bar_cache_get_bars for SPY us_etf 1m "
    "{from_y:d}-{from_m:d}-{from_d:d} to "
    "{to_y:d}-{to_m:d}-{to_d:d} with allow_partial"
)
def step_call_get_bars_allow_partial(
    context, from_y, from_m, from_d, to_y, to_m, to_d,
):
    _ensure_tmp_home(context)
    context.sa_response = mcp_tools.bar_cache_get_bars(
        canonical="SPY", asset_class="us_etf", resolution="1m",
        from_date=f"{from_y:04d}-{from_m:02d}-{from_d:02d}",
        to_date=f"{to_y:04d}-{to_m:02d}-{to_d:02d}",
        allow_partial=True,
        summary_only=True,
    )


def _ensure_tmp_home(context):
    """For scenarios that don't explicitly redirect Path.home(),
    create a tmpdir so the test never writes to the operator's
    real ~/.tradepro/bar_cache."""
    if not hasattr(context, "_tmp_home"):
        context._tmp_home = tempfile.mkdtemp(prefix="mcp_bar_cache_")
        import pathlib
        context._original_home = pathlib.Path.home
        pathlib.Path.home = staticmethod(
            lambda: pathlib.Path(context._tmp_home)
        )  # type: ignore[assignment]
        context.add_cleanup(_restore_home, context)
