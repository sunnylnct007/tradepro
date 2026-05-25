"""Steps for llm_signal_gate.feature.

All scenarios are synthetic: no network, no real LLM provider, no real
yfinance. News fetcher and scorer are injected via the LLMSignalGate's
_news_fn / _score_fn hooks; the StrategyConfigRegistry uses a tmp path.
"""
from __future__ import annotations

import json
import pathlib
import tempfile

from behave import given, then, when

from tradepro_strategies.news import NewsItem
from tradepro_strategies.news_sentiment import ScoredHeadline
from tradepro_strategies.paper.llm_gate import (
    GateDecision,
    LLMGateConfig,
    LLMSignalGate,
)
from tradepro_strategies.paper.overrides import (
    OverrideAction,
    OverrideRegistry,
    StrategyOverride,
)
from tradepro_strategies.paper.strategy_config import (
    StrategyConfig,
    StrategyConfigRegistry,
)
from tradepro_strategies.paper.strategy_runner import StrategyRunner


# ====================================================================== #
# Helper builders                                                          #
# ====================================================================== #


def _make_news_fn(headlines: list[str]):
    """Returns a function that produces fake NewsItem objects."""
    items = [
        NewsItem(title=h, publisher="test", link=None,
                 published_at=None, thumbnail=None)
        for h in headlines
    ]

    def _fn(symbol, max_items=5):
        return items[:max_items]

    return _fn


def _make_score_fn(sentiments: list[float], material: list[bool] | None = None):
    """Returns a function that produces ScoredHeadline objects."""
    def _fn(items, provider):
        result = []
        for i, item in enumerate(items):
            s = sentiments[i] if i < len(sentiments) else 0.0
            m = (material[i] if material and i < len(material) else True)
            result.append(ScoredHeadline(
                title=item.title,
                sentiment=s,
                themes=[],
                material=m,
                model="test",
                error=None,
            ))
        return result

    return _fn


def _make_registry() -> StrategyConfigRegistry:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="strat-cfg-")) / "test_configs.json"
    return StrategyConfigRegistry(path=tmp)


def _make_runner() -> StrategyRunner:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="runner-"))
    reg = StrategyConfigRegistry(path=tmp / "configs.json")
    ov = OverrideRegistry(path=tmp / "overrides.json")
    return StrategyRunner(config_registry=reg, override_registry=ov)


# ====================================================================== #
# Section A: LLMGateConfig                                                 #
# ====================================================================== #


@given("a default LLMGateConfig")
def step_default_config(context) -> None:
    context.gate_config = LLMGateConfig()


@then("the gate config is enabled")
def step_config_enabled(context) -> None:
    assert context.gate_config.enabled is True, context.gate_config


@then("the veto threshold is {value:f}")
def step_veto_threshold(context, value: float) -> None:
    assert context.gate_config.sentiment_veto_below == value, (
        f"expected {value}, got {context.gate_config.sentiment_veto_below}"
    )


@then("the boost threshold is {value:f}")
def step_boost_threshold(context, value: float) -> None:
    assert context.gate_config.sentiment_boost_above == value, (
        f"expected {value}, got {context.gate_config.sentiment_boost_above}"
    )


@then("fail_open is True")
def step_fail_open_true(context) -> None:
    assert context.gate_config.fail_open is True


@when("I serialize the config to a dict and back")
def step_roundtrip(context) -> None:
    d = context.gate_config.to_dict()
    context.roundtripped = LLMGateConfig.from_dict(d)


@then("the round-tripped config equals the original")
def step_roundtrip_equal(context) -> None:
    assert context.roundtripped == context.gate_config, (
        f"orig={context.gate_config} round={context.roundtripped}"
    )


# ====================================================================== #
# Section B: GateDecision basic logic                                      #
# ====================================================================== #


@given("an LLMSignalGate with enabled=False")
def step_gate_disabled(context) -> None:
    cfg = LLMGateConfig(enabled=False)
    context.gate = LLMSignalGate(cfg)


@given("an LLMSignalGate with default config")
def step_gate_default(context) -> None:
    # Inject empty news so default-config evaluations don't try to hit
    # the network on signal != 0 paths.
    context.gate = LLMSignalGate(
        LLMGateConfig(),
        _news_fn=_make_news_fn([]),
        _score_fn=_make_score_fn([]),
    )


@when('I evaluate symbol "{symbol}" signal {signal:f}')
def step_evaluate(context, symbol: str, signal: float) -> None:
    context.decision = context.gate.evaluate(symbol, signal)


@then('the decision action is "{action}"')
def step_decision_action(context, action: str) -> None:
    assert context.decision.action == action, (
        f"expected {action!r}, got {context.decision.action!r}; reason={context.decision.reason}"
    )


@then("the decision scale_factor is {value:f}")
def step_decision_scale(context, value: float) -> None:
    assert abs(context.decision.scale_factor - value) < 1e-9, (
        f"expected {value}, got {context.decision.scale_factor}"
    )


@then('the decision reason mentions "{needle}"')
def step_decision_reason_contains(context, needle: str) -> None:
    assert needle in context.decision.reason, (
        f"expected {needle!r} in reason, got {context.decision.reason!r}"
    )


@then("the decision headlines_checked is {count:d}")
def step_decision_headlines(context, count: int) -> None:
    assert context.decision.headlines_checked == count, (
        f"expected {count}, got {context.decision.headlines_checked}"
    )


# ====================================================================== #
# Section C: Injected news/score                                           #
# ====================================================================== #


@given("an LLMSignalGate with injected news of {n:d} headline")
@given("an LLMSignalGate with injected news of {n:d} headlines")
def step_gate_with_news(context, n: int) -> None:
    titles = [f"Headline {i}" for i in range(n)]
    context._news_fn = _make_news_fn(titles)
    context._score_fn = None  # set later by the scorer step
    context._build_gate = lambda cfg=None: LLMSignalGate(
        cfg or LLMGateConfig(),
        _news_fn=context._news_fn,
        _score_fn=context._score_fn,
    )


@given("an LLMSignalGate with no headlines")
def step_gate_with_no_news(context) -> None:
    context.gate = LLMSignalGate(
        LLMGateConfig(),
        _news_fn=_make_news_fn([]),
        _score_fn=_make_score_fn([]),
    )


@given("the injected scorer returns sentiments [{sentiments}] with material [{material}]")
def step_inject_scorer(context, sentiments: str, material: str) -> None:
    sents = [float(s.strip()) for s in sentiments.split(",") if s.strip()]
    mats = [m.strip() == "True" for m in material.split(",") if m.strip()]
    context._score_fn = _make_score_fn(sents, mats)
    # Build the gate now that both fns are known.
    context.gate = LLMSignalGate(
        LLMGateConfig(),
        _news_fn=context._news_fn,
        _score_fn=context._score_fn,
    )


@given("an LLMSignalGate that always raises on scoring")
def step_gate_raises(context) -> None:
    def _bad_score(items, provider):
        raise RuntimeError("synthetic LLM failure")

    context.gate = LLMSignalGate(
        LLMGateConfig(),
        _news_fn=_make_news_fn(["Some headline"]),
        _score_fn=_bad_score,
    )


# ====================================================================== #
# Section D: StrategyConfigRegistry                                        #
# ====================================================================== #


@given("a fresh StrategyConfigRegistry")
def step_fresh_registry(context) -> None:
    context.registry = _make_registry()


@when('I get the config for "{name}"')
def step_get_config(context, name: str) -> None:
    context.fetched_config = context.registry.get(name)


@then("the config is enabled")
def step_config_is_enabled(context) -> None:
    assert context.fetched_config.enabled is True, context.fetched_config


@then("the config params dict is empty")
def step_config_params_empty(context) -> None:
    assert context.fetched_config.params == {}, context.fetched_config.params


@then("the config llm_gate is the default gate config")
def step_config_llm_default(context) -> None:
    assert context.fetched_config.llm_gate == LLMGateConfig().to_dict(), (
        context.fetched_config.llm_gate
    )


@when('I update params for "{name}" with {payload}')
def step_update_params(context, name: str, payload: str) -> None:
    data = json.loads(payload)
    context.registry.update_params(name, data)


@then('the stored params for "{name}" equal {payload}')
def step_params_equal(context, name: str, payload: str) -> None:
    expected = json.loads(payload)
    actual = context.registry.get(name).params
    assert actual == expected, f"expected {expected}, got {actual}"


@then('the stored params for "{name}" contain key "{key}" with value {value:f}')
def step_params_have_key(context, name: str, key: str, value: float) -> None:
    actual = context.registry.get(name).params
    assert key in actual, f"key {key!r} missing from {actual!r}"
    assert float(actual[key]) == value, (
        f"expected {key}={value}, got {actual[key]}"
    )


@when('I update the LLM gate for "{name}" with enabled={flag}')
def step_update_llm_gate(context, name: str, flag: str) -> None:
    enabled = flag.strip() == "True"
    gate_cfg = LLMGateConfig(enabled=enabled)
    context.registry.update_llm_gate(name, gate_cfg)


@then('to_status_dict for "{name}" shows llm_gate enabled {flag}')
def step_status_llm_enabled(context, name: str, flag: str) -> None:
    expected = flag.strip() == "True"
    status = context.registry.to_status_dict(name)
    actual = status["llm_gate"]["enabled"]
    assert actual is expected, f"expected {expected}, got {actual}"


# ====================================================================== #
# Section E: StrategyRunner                                                #
# ====================================================================== #


@given("a fresh StrategyRunner")
def step_fresh_runner(context) -> None:
    context.runner = _make_runner()


@when('I configure strategy "{name}" enabled {flag}')
def step_configure_strategy(context, name: str, flag: str) -> None:
    enabled = flag.strip() == "True"
    cfg = StrategyConfig(
        strategy_name=name,
        params={},
        llm_gate=LLMGateConfig().to_dict(),
        enabled=enabled,
    )
    context.runner.config_registry.set(cfg)


@when('I pause strategy "{name}" via the override registry')
def step_pause_strategy(context, name: str) -> None:
    context.runner.override_registry.apply(StrategyOverride(
        strategy_name=name,
        action=OverrideAction.PAUSE,
    ))


@then('get_active_strategies includes "{name}"')
def step_active_includes(context, name: str) -> None:
    active = context.runner.get_active_strategies()
    assert name in active, f"expected {name} in {active}"


@then('get_active_strategies does not include "{name}"')
def step_active_excludes(context, name: str) -> None:
    active = context.runner.get_active_strategies()
    assert name not in active, f"expected {name} NOT in {active}"


@when('I call build_strategy for "{name}"')
def step_build(context, name: str) -> None:
    context.build_result = context.runner.build_strategy(name)


@then("the build result is None")
def step_build_none(context) -> None:
    assert context.build_result is None, f"expected None, got {context.build_result!r}"


@then('the status row for "{name}" has keys strategy_name, enabled, paused, llm_gate_enabled')
def step_status_keys(context, name: str) -> None:
    status = context.runner.status()
    rows = {r["strategy_name"]: r for r in status["strategies"]}
    assert name in rows, f"{name} not in status rows: {list(rows)}"
    row = rows[name]
    for key in ("strategy_name", "enabled", "paused", "llm_gate_enabled"):
        assert key in row, f"missing key {key!r} in {row!r}"
