"""Steps for asset_class_breadth.feature — covers the four new
plugins (us_equity, uk_equity, fx_spot, crypto) plus regression-
guards us_etf. Pure imports — no provider / network calls."""
from __future__ import annotations

from datetime import date

from behave import given, then, when

# Importing the package triggers register_asset_class() on every plugin.
from tradepro_strategies.bar_cache import asset_classes  # noqa: F401
from tradepro_strategies.bar_cache.asset_class import (
    get_asset_class,
    list_asset_classes,
)


@when('I list the registered asset classes')
def step_list_registered(context):
    context.registered = set(list_asset_classes())


@then('the registry contains "{name}"')
def step_assert_registered(context, name):
    assert name in context.registered, (
        f"expected {name!r} in {sorted(context.registered)}"
    )


@given('the asset class "{name}"')
def step_pick_plugin(context, name):
    context.plugin = get_asset_class(name)


@then('the supported resolutions are "{csv}"')
def step_assert_resolutions(context, csv):
    expected = tuple(x.strip() for x in csv.split(","))
    got = context.plugin.supported_resolutions()
    assert got == expected, (
        f"expected resolutions {expected}, got {got}"
    )


@when('I ask for expected sessions between {start} and {end}')
def step_session_dates(context, start, end):
    start_d = date.fromisoformat(start.strip())
    end_d = date.fromisoformat(end.strip())
    context.sessions = set(
        context.plugin.expected_session_dates(start_d, end_d)
    )


@then('the session list excludes {d}')
def step_assert_excludes(context, d):
    target = date.fromisoformat(d.strip())
    assert target not in context.sessions, (
        f"expected {target} NOT in sessions; got {sorted(context.sessions)}"
    )


@then('the session list includes {d}')
def step_assert_includes(context, d):
    target = date.fromisoformat(d.strip())
    assert target in context.sessions, (
        f"expected {target} in sessions; got {sorted(context.sessions)}"
    )


@then('the session list has {n:d} entries')
def step_assert_count(context, n):
    assert len(context.sessions) == n, (
        f"expected {n} session entries, got {len(context.sessions)}: "
        f"{sorted(context.sessions)}"
    )


@then('the expected {resolution} bar count for {d} is {n:d}')
def step_assert_bar_count(context, resolution, d, n):
    target = date.fromisoformat(d.strip())
    got = context.plugin.expected_bar_count(resolution.strip(), target)
    assert got == n, (
        f"expected {n} bars for {target} @ {resolution}, got {got}"
    )


@then('volume is in the nullable column set')
def step_volume_nullable(context):
    assert "volume" in context.plugin.schema.nullable_columns, (
        f"volume not nullable; schema={context.plugin.schema}"
    )


@then('volume is NOT in the nullable column set')
def step_volume_not_nullable(context):
    assert "volume" not in context.plugin.schema.nullable_columns, (
        f"volume unexpectedly nullable; schema={context.plugin.schema}"
    )


@then('the schema_version is "{expected}"')
def step_schema_version(context, expected):
    got = context.plugin.schema.schema_version
    assert got == expected, (
        f"expected schema_version {expected!r}, got {got!r}"
    )
