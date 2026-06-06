"""Steps for backtest_preflight_default.feature — exercises the
default-on + auto-resolve preflight path. Reuses the
quant_backtest._run_preflight helper directly (the .NET / BarStore
side is faked via patching preflight_data_state)."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from behave import then, when

from tradepro_strategies.bar_cache import PreflightResult
from tradepro_strategies.cli.quant_backtest import _run_preflight


def _make_fake_preflight(specs: dict[str, dict]):
    """Match the shape used in quant_backtest_preflight_steps so the
    fake plays nicely with both suites."""

    def fake(canonical, asset_class, resolution, start, end, **kwargs):
        spec = specs[canonical]
        return PreflightResult(
            data_state_hash=spec["hash"],
            coverage_complete=spec["coverage_complete"],
            rows_returned=100,
            rows_expected=100 if spec["coverage_complete"] else 50,
            partitions_used=[f"{canonical}-2026-01"],
            provider_used="fake",
            schema_version=f"{asset_class}_v1",
            base_dir="/tmp/fake",
        )

    return fake


@when('the quant preflight runs with auto-resolved asset classes at '
      'resolution "{resolution}"')
def step_run_preflight_auto(context, resolution):
    symbols = list(context.preflight_specs.keys())
    fake = _make_fake_preflight(context.preflight_specs)
    with patch(
        "tradepro_strategies.bar_cache.preflight_data_state",
        side_effect=fake,
    ):
        context.preflight_result = _run_preflight(
            symbols=symbols,
            start=datetime(2026, 1, 1),
            end=datetime(2026, 1, 31),
            asset_class=None,           # ← auto-resolver path
            resolution=resolution,
            api_base=None,
            allow_incomplete=False,
        )


@when('the quant preflight runs with explicit asset_class "{asset_class}" '
      'at resolution "{resolution}"')
def step_run_preflight_explicit(context, asset_class, resolution):
    symbols = list(context.preflight_specs.keys())
    fake = _make_fake_preflight(context.preflight_specs)
    with patch(
        "tradepro_strategies.bar_cache.preflight_data_state",
        side_effect=fake,
    ):
        context.preflight_result = _run_preflight(
            symbols=symbols,
            start=datetime(2026, 1, 1),
            end=datetime(2026, 1, 31),
            asset_class=asset_class,
            resolution=resolution,
            api_base=None,
            allow_incomplete=False,
        )


@then('the resolved class for {ticker} is "{expected}"')
def step_assert_resolved_class(context, ticker, expected):
    resolved = context.preflight_result["data_state"]["resolved_classes"]
    got = resolved.get(ticker)
    assert got == expected, (
        f"resolved_classes[{ticker!r}] = {got!r}, expected {expected!r}; "
        f"full map = {resolved}"
    )


@then('the resolved_by value is "{expected}"')
def step_assert_resolved_by(context, expected):
    got = context.preflight_result["data_state"].get("resolved_by")
    assert got == expected, (
        f"resolved_by = {got!r}, expected {expected!r}"
    )
