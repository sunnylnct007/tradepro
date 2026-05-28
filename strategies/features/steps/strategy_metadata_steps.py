"""Steps for strategy_metadata.feature — pins the source / status /
default_lookback_days contract on Strategy subclasses + catalog push
+ daemon lookup so future refactors can't silently strip the metadata."""
from __future__ import annotations

from behave import given, then, when

from tradepro_strategies.cli.paper_daemon import _parse_params
from tradepro_strategies.cli.paper_strategies_push import build_catalog
from tradepro_strategies.paper import registry as strategy_registry


@given("the paper strategies package is imported")
def step_import_package(context) -> None:
    # Triggers every @register_strategy decorator in the package's
    # __init__.py. After this call the registry is populated.
    import tradepro_strategies.paper.strategies  # noqa: F401
    context.registry = strategy_registry


@then("every registered strategy class declares source")
def step_every_has_source(context) -> None:
    for name in context.registry.list_names():
        cls = context.registry.get(name).cls
        assert hasattr(cls, "source") and isinstance(cls.source, str), (
            f"{name}: source missing or non-string"
        )


@then("every registered strategy class declares status")
def step_every_has_status(context) -> None:
    for name in context.registry.list_names():
        cls = context.registry.get(name).cls
        assert hasattr(cls, "status") and isinstance(cls.status, str), (
            f"{name}: status missing or non-string"
        )


@then("every registered strategy class declares default_lookback_days")
def step_every_has_lookback(context) -> None:
    for name in context.registry.list_names():
        cls = context.registry.get(name).cls
        v = getattr(cls, "default_lookback_days", None)
        assert isinstance(v, int) and v >= 0, (
            f"{name}: default_lookback_days missing or invalid ({v!r})"
        )


@when('I look up the "{name}" strategy class')
def step_lookup(context, name: str) -> None:
    context.cls = context.registry.get(name).cls


@then('its source is "{expected}"')
def step_source_is(context, expected: str) -> None:
    assert context.cls.source == expected, (
        f"expected source={expected!r}, got {context.cls.source!r}"
    )


@then("its default_lookback_days is {n:d}")
def step_lookback_is(context, n: int) -> None:
    assert context.cls.default_lookback_days == n, (
        f"expected default_lookback_days={n}, got {context.cls.default_lookback_days}"
    )


@when("I build the catalog payload")
def step_build_catalog(context) -> None:
    context.catalog = build_catalog()


@then("every catalog entry carries source, status, default_lookback_days")
def step_catalog_entries_have_metadata(context) -> None:
    for entry in context.catalog["strategies"]:
        for key in ("source", "status", "default_lookback_days"):
            assert key in entry, f"entry {entry.get('name')!r} missing {key}"


@then(
    'the ichimoku_fx_mr entry has source "{src}" and default_lookback_days {n:d}'
)
def step_catalog_fx_entry(context, src: str, n: int) -> None:
    entries = [e for e in context.catalog["strategies"] if e["name"] == "ichimoku_fx_mr"]
    assert len(entries) == 1, f"expected 1 entry, found {len(entries)}"
    e = entries[0]
    assert e["source"] == src, f"source: {e['source']!r}"
    assert e["default_lookback_days"] == n, f"lookback: {e['default_lookback_days']}"


@given(
    'trigger params with strategy "{strategy}" and lookback_days {n:d}'
)
def step_trigger_params(context, strategy: str, n: int) -> None:
    # The daemon's _parse_params reads `raw.params` (or wraps it in a
    # session envelope). Flat shape is the SQS message form.
    context.raw_params = {
        "params": {
            "strategy": strategy,
            "symbols": [],
            "lookback_days": n,
        }
    }


@when("the daemon parses the params")
def step_parse(context) -> None:
    context.parsed = _parse_params(context.raw_params, default_broker="t212")


@then("the resolved lookback_days is {n:d}")
def step_resolved_lookback(context, n: int) -> None:
    assert context.parsed["lookback_days"] == n, (
        f"expected {n}, got {context.parsed['lookback_days']}"
    )
