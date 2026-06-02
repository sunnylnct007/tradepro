"""Steps for quant_backtest_preflight.feature — covers the D-3 / E
migration of `tradepro-quant-backtest`. The end-to-end scenarios
monkey-patch `preflight_data_state` inside the bar_cache module so
the BDD suite never touches BarStore / yfinance."""
from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import patch

from behave import given, then, when

from tradepro_strategies.bar_cache import PreflightResult
from tradepro_strategies.cli.quant_backtest import (
    _combine_data_state_hashes,
    _run_preflight,
)


# ── Combined-hash scenarios ───────────────────────────────────────


def _hashes_from_table(table) -> dict[str, str]:
    return {row["symbol"]: row["hash"] for row in table}


@given('per-symbol hashes')
def step_per_symbol_hashes(context):
    context.per_symbol = _hashes_from_table(context.table)


@when('the combined hash is computed')
def step_compute_combined(context):
    context.combined = _combine_data_state_hashes(context.per_symbol)


@when('the combined hash is computed again')
def step_compute_combined_again(context):
    context.combined_after = _combine_data_state_hashes(context.per_symbol)


@when('SPY\'s hash becomes "{new_hash}"')
def step_mutate_spy(context, new_hash):
    context.per_symbol["SPY"] = new_hash


@then('the combined hash equals the SHA256 of the sorted lines')
def step_assert_sha256(context):
    lines = sorted(f"{s}:{h}" for s, h in context.per_symbol.items())
    expected = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    assert context.combined == expected, (
        f"expected {expected}, got {context.combined}"
    )


@then('reordering the input symbols produces the same combined hash')
def step_assert_order_independent(context):
    shuffled = dict(reversed(list(context.per_symbol.items())))
    again = _combine_data_state_hashes(shuffled)
    assert again == context.combined, (
        f"order changed result: {context.combined} != {again}"
    )


@then('the combined hash equals "{expected}"')
def step_assert_combined_equals(context, expected):
    assert context.combined == expected, (
        f"expected {expected!r}, got {context.combined!r}"
    )


@then('the two combined hashes differ')
def step_assert_combined_differ(context):
    assert context.combined != context.combined_after, (
        f"hashes did not differ: both = {context.combined}"
    )


# ── End-to-end preflight scenarios (injected fake) ────────────────


def _make_fake_preflight(per_symbol_specs: dict[str, dict]):
    """Return a callable that mimics ``preflight_data_state``. Per-symbol
    specs map canonical → {hash, coverage_complete}."""

    def fake(canonical, asset_class, resolution, start, end, **kwargs):
        spec = per_symbol_specs[canonical]
        return PreflightResult(
            data_state_hash=spec["hash"],
            coverage_complete=spec["coverage_complete"],
            rows_returned=100,
            rows_expected=100 if spec["coverage_complete"] else 50,
            partitions_used=[f"{canonical}-2026-01"],
            provider_used="fake",
            schema_version="us_equity_v1",
            base_dir="/tmp/fake",
        )

    return fake


@given('a fake preflight that returns')
def step_install_fake_preflight(context):
    specs: dict[str, dict] = {}
    for row in context.table:
        specs[row["symbol"]] = {
            "hash": row["hash"],
            "coverage_complete": row["coverage_complete"].lower() == "true",
        }
    context.preflight_specs = specs


def _invoke_preflight(context, asset_class, resolution, allow_incomplete):
    symbols = list(context.preflight_specs.keys())
    fake = _make_fake_preflight(context.preflight_specs)
    # Patch where _run_preflight imports it from. The helper does the
    # import inside the function body so we have to patch the actual
    # source location.
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
            allow_incomplete=allow_incomplete,
        )


@when('the quant preflight runs for asset_class "{asset}" resolution "{resolution}"')
def step_run_preflight(context, asset, resolution):
    _invoke_preflight(context, asset, resolution, allow_incomplete=False)


@when('the quant preflight runs for asset_class "{asset}" resolution "{resolution}" '
      'allowing incomplete data')
def step_run_preflight_allow(context, asset, resolution):
    _invoke_preflight(context, asset, resolution, allow_incomplete=True)


@then('the per_symbol_states block carries {n:d} entries')
def step_assert_per_symbol_entries(context, n):
    states = context.preflight_result["data_state"]["per_symbol_states"]
    assert len(states) == n, f"expected {n} entries, got {len(states)}"


@then('the combined data_state_hash is non-empty')
def step_assert_non_empty_hash(context):
    h = context.preflight_result["data_state_hash"]
    assert h and h != "EMPTY_DATA_STATE", (
        f"expected non-empty real hash, got {h!r}"
    )


@then('the combined data_state_hash is still produced')
def step_assert_combined_still_produced(context):
    h = context.preflight_result["data_state_hash"]
    assert h, f"expected a hash even on incomplete coverage, got {h!r}"


@then('coverage_complete is true at the top level')
def step_assert_coverage_complete_true(context):
    cc = context.preflight_result["data_state"]["coverage_complete"]
    assert cc is True, f"expected coverage_complete True, got {cc!r}"


@then('coverage_complete is false at the top level')
def step_assert_coverage_complete_false(context):
    cc = context.preflight_result["data_state"]["coverage_complete"]
    assert cc is False, f"expected coverage_complete False, got {cc!r}"
