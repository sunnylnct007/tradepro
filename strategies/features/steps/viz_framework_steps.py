"""Steps for viz_framework.feature — pin the chart registry contract
+ smoke-test the trader-anchor builders return valid Plotly JSON.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.viz import build_chart, list_charts


@given("the viz registry is loaded")
def step_registry_loaded(context):
    # Import re-runs the package __init__, which triggers all
    # @register_chart decorators in the submodules.
    from tradepro_strategies import viz  # noqa: F401
    context.charts = {c.name: c for c in list_charts()}


@then('chart "{name}" is registered')
def step_chart_registered(context, name):
    assert name in context.charts, (
        f"Chart {name!r} not in registry. Found: {sorted(context.charts)}"
    )


@then("each registered chart declares a non-empty description")
def step_descriptions(context):
    missing = [n for n, c in context.charts.items() if not c.description]
    assert not missing, f"Charts without description: {missing}"


@when('I try to build chart "{name}"')
def step_try_build_unknown(context, name):
    try:
        build_chart(name)
        context.error = None
    except KeyError as e:
        context.error = e


@then("a KeyError is raised mentioning the available chart names")
def step_keyerror_helpful(context):
    assert isinstance(context.error, KeyError), (
        f"expected KeyError, got {type(context.error).__name__}"
    )
    msg = str(context.error)
    # Both anchor charts should appear in the error so the operator
    # knows what's available without grepping.
    assert "backtest_4panel" in msg and "monte_carlo_fan" in msg, msg


@given("a synthetic EnsembleResult covering 100 trading days with two sleeves")
def step_synthetic_ensemble(context):
    rng = np.random.default_rng(seed=42)
    idx = pd.date_range("2024-01-01", periods=100, freq="B")
    sleeve_a = pd.Series(rng.normal(0.0005, 0.01, len(idx)), index=idx)
    sleeve_b = pd.Series(rng.normal(0.0007, 0.012, len(idx)), index=idx)
    combined = (sleeve_a + sleeve_b) / 2.0
    equity = (1 + combined).cumprod() * 100_000
    context.ensemble = SimpleNamespace(
        equity=equity,
        daily_returns=combined,
        sleeve_returns={"alpha": sleeve_a, "beta": sleeve_b},
        vol_scalar=pd.Series(1.0, index=idx),
        summary={
            "cagr_pct": 8.2,
            "sharpe": 1.1,
            "max_drawdown_pct": -7.5,
        },
    )


@given("a synthetic SPY benchmark series")
def step_synthetic_spy(context):
    rng = np.random.default_rng(seed=7)
    idx = context.ensemble.equity.index
    rets = rng.normal(0.0004, 0.011, len(idx))
    spy_equity = (1 + pd.Series(rets, index=idx)).cumprod() * 100_000
    context.spy_equity = spy_equity
    context.spy_summary = {
        "cagr_pct": 6.5,
        "sharpe": 0.8,
        "max_drawdown_pct": -10.4,
    }


@when('I build chart "backtest_4panel"')
def step_build_backtest_4panel(context):
    context.figure = build_chart(
        "backtest_4panel",
        result=context.ensemble,
        spy_equity=context.spy_equity,
        spy_summary=context.spy_summary,
    )


@given("a synthetic MonteCarloResult with 200 paths over 5 years")
def step_synthetic_mc(context):
    rng = np.random.default_rng(seed=1)
    n_sims, n_days = 200, 5 * 252
    starts = np.full((n_sims, 1), 100_000.0)
    rets = rng.normal(0.0005, 0.012, (n_sims, n_days))
    paths = np.hstack([starts, starts * (1 + rets).cumprod(axis=1)])
    context.mc = SimpleNamespace(
        paths=paths,
        summary={},
        n_sims=n_sims,
        years=5,
    )


@when('I build chart "monte_carlo_fan"')
def step_build_mc_fan(context):
    context.figure = build_chart("monte_carlo_fan", mc=context.mc)


@then('the figure JSON has a "data" key with at least {n:d} traces')
def step_data_traces(context, n):
    assert "data" in context.figure, "no data key in figure"
    assert len(context.figure["data"]) >= n, (
        f"expected ≥{n} traces, got {len(context.figure['data'])}"
    )


@then('the figure JSON has a "layout" key with non-empty annotations')
def step_layout_annotations(context):
    layout = context.figure.get("layout") or {}
    annotations = layout.get("annotations") or []
    assert annotations, "expected at least one annotation (subplot titles)"


@then("the figure JSON has a histogram trace among its data")
def step_histogram_present(context):
    types = [t.get("type") for t in context.figure["data"]]
    assert "histogram" in types, (
        f"expected a histogram trace, got types={types}"
    )


@then("the figure JSON round-trips through json.dumps without error")
def step_json_roundtrip(context):
    # Plotly figures use numpy types, which json.dumps refuses by
    # default. fig.to_plotly_json() should already convert. If a new
    # chart slips raw numpy in we want the test to fail loudly.
    s = json.dumps(context.figure)
    assert len(s) > 0
