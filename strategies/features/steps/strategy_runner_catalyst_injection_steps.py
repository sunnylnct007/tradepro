"""Steps for strategy_runner_catalyst_injection.feature — exercises
the Phase C-3.3 auto-injection of CatalystFetcher into every built
strategy. Uses temp registries (same pattern as
llm_signal_gate_steps) so we don't touch persisted config."""
from __future__ import annotations

import pathlib
import tempfile

from behave import given, then, when

from tradepro_strategies.paper.catalysts_fetcher import CatalystFetcher
from tradepro_strategies.paper.overrides import OverrideRegistry
from tradepro_strategies.paper.strategy_config import (
    StrategyConfig,
    StrategyConfigRegistry,
)
from tradepro_strategies.paper.strategy_runner import StrategyRunner


def _new_registries() -> tuple[StrategyConfigRegistry, OverrideRegistry]:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="c33-"))
    reg = StrategyConfigRegistry(path=tmp / "configs.json")
    ov = OverrideRegistry(path=tmp / "overrides.json")
    return reg, ov


def _seed_config(
    reg: StrategyConfigRegistry, name: str, params: dict | None = None,
) -> None:
    """Persist a minimal config so build_strategy() finds the row."""
    reg.set(StrategyConfig(
        strategy_name=name,
        enabled=True,
        params=params or {},
        llm_gate={},
    ))


@given('a StrategyRunner without a catalysts api_base')
def step_runner_no_api_base(context):
    reg, ov = _new_registries()
    context.config_registry = reg
    context.override_registry = ov
    context.runner = StrategyRunner(
        config_registry=reg, override_registry=ov,
    )


@given('a StrategyRunner with a catalysts api_base "{api_base}"')
def step_runner_with_api_base(context, api_base):
    reg, ov = _new_registries()
    context.config_registry = reg
    context.override_registry = ov
    context.runner = StrategyRunner(
        config_registry=reg, override_registry=ov,
        catalysts_api_base=api_base,
        catalysts_api_token="test-token",
    )


@when('I call build_catalyst_fetcher')
def step_call_build_fetcher(context):
    context.built_fetcher = context.runner.build_catalyst_fetcher()


@then('the returned fetcher is None')
def step_assert_built_fetcher_none(context):
    assert context.built_fetcher is None, (
        f"expected None, got {context.built_fetcher!r}"
    )


@then('the returned fetcher is a CatalystFetcher')
def step_assert_built_fetcher_type(context):
    assert isinstance(context.built_fetcher, CatalystFetcher), (
        f"expected CatalystFetcher, got {type(context.built_fetcher).__name__}"
    )


@when('I build the "{name}" strategy')
def step_build_strategy(context, name):
    # Seed a default config only if the scenario didn't already
    # provide one (the explicit-override scenario seeds in @given).
    if context.config_registry.get(name).params == {}:
        _seed_config(context.config_registry, name)
    strat = context.runner.build_strategy(name)
    if not hasattr(context, "built"):
        context.built = {}
    context.built[name] = strat


@then("the built strategy's _catalyst_fetcher is None")
def step_assert_fetcher_none(context):
    last = next(reversed(context.built))
    strat = context.built[last]
    assert strat is not None, f"build_strategy returned None for {last}"
    fetcher = getattr(strat, "_catalyst_fetcher", None)
    assert fetcher is None, f"expected None, got {fetcher!r}"


@then("the built strategy's _catalyst_fetcher is not None")
def step_assert_fetcher_set(context):
    last = next(reversed(context.built))
    strat = context.built[last]
    fetcher = getattr(strat, "_catalyst_fetcher", None)
    assert isinstance(fetcher, CatalystFetcher), (
        f"expected CatalystFetcher, got {type(fetcher).__name__}"
    )


@then("both built strategies share the same _catalyst_fetcher instance")
def step_assert_shared_fetcher(context):
    fetchers = {
        name: getattr(strat, "_catalyst_fetcher", None)
        for name, strat in context.built.items()
    }
    distinct = {id(f) for f in fetchers.values()}
    assert len(distinct) == 1, (
        f"expected one shared instance, got {len(distinct)}: {fetchers}"
    )


