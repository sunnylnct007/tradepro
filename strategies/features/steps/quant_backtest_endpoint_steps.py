"""Steps for quant_backtest_endpoint.feature — end-to-end exercise of
the worker CLI without hitting yfinance.

We monkeypatch the loader the CLI uses (_load_sleeve_data and
_spy_benchmark) to return deterministic synthetic OHLC. Everything
downstream (Sleeve, Ensemble, MonteCarloSimulator, viz.build_chart)
runs for real so the test catches regressions in either the engine
or the charting layer.
"""
from __future__ import annotations

import ast
import json

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.cli import quant_backtest as cli


def _synthetic_ohlc(symbol: str, n_bars: int = 200, seed: int = 1) -> pd.DataFrame:
    """Deterministic OHLC. Seed from the symbol so different tickers
    get distinct (but reproducible) paths."""
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32) + seed)
    idx = pd.date_range("2024-01-02", periods=n_bars, freq="B")
    rets = rng.normal(0.0005, 0.012, n_bars)
    close = 100.0 * (1 + rets).cumprod()
    high = close * (1 + np.abs(rng.normal(0, 0.005, n_bars)))
    low = close * (1 - np.abs(rng.normal(0, 0.005, n_bars)))
    open_ = close * (1 + rng.normal(0, 0.001, n_bars))
    return pd.DataFrame({
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": rng.integers(1_000_000, 5_000_000, n_bars),
    }, index=idx)


@given("a stubbed data loader returning 200 synthetic daily bars per symbol")
def step_stub_loader(context):
    def _stub_load(symbols, start, end, provider="yahoo"):  # noqa: ARG001
        return {s: _synthetic_ohlc(s, n_bars=200) for s in symbols}

    def _stub_spy(start, end, initial_capital):  # noqa: ARG001
        df = _synthetic_ohlc("SPY", n_bars=200, seed=99)
        rets = df["Close"].pct_change().fillna(0.0)
        equity = (1 + rets).cumprod() * initial_capital
        # Reuse the engine's summarise so the chart's title block works.
        from tradepro_strategies.quant_engine.portfolio_metrics import summarise
        return equity, summarise(equity, rets)

    context._orig_load = cli._load_sleeve_data
    context._orig_spy = cli._spy_benchmark
    cli._load_sleeve_data = _stub_load
    cli._spy_benchmark = _stub_spy

    def _restore():
        cli._load_sleeve_data = context._orig_load
        cli._spy_benchmark = context._orig_spy
    context.add_cleanup(_restore)


def _parse_symbol_list(raw: str) -> list[str]:
    """Parse a bracketed list from the feature step phrase."""
    # ``symbols=["AAA","BBB"]`` → ``["AAA","BBB"]``
    return ast.literal_eval(raw)


@when('I run quant_backtest with payload symbols={symbols} and {n_sims:d} monte-carlo sims')
def step_run_backtest(context, symbols, n_sims):
    sym_list = _parse_symbol_list(symbols)
    payload = {
        "kind": "backtest",
        "strategy": "ichimoku_equity",
        "symbols": sym_list,
        "start": "2024-01-02",
        "end": "2024-10-31",
        "initial_capital": 100_000.0,
        "monte_carlo": {"n_sims": n_sims, "years": 2, "seed": 7},
        "label": "behave-test",
    }
    try:
        context.result_summary = cli.run_backtest_from_payload(payload)
        context.error = None
    except SystemExit as exc:
        context.result_summary = None
        context.error = exc


@then('the result_summary has kind "backtest"')
def step_kind_is_backtest(context):
    assert context.result_summary is not None, "no result_summary (did the CLI fail?)"
    assert context.result_summary.get("kind") == "backtest", context.result_summary.get("kind")


@then('the result_summary.charts contains key "{chart_name}"')
def step_charts_contains(context, chart_name):
    charts = context.result_summary.get("charts") or {}
    assert chart_name in charts, f"missing {chart_name!r}; found {sorted(charts)}"


@then("the result_summary is JSON-serialisable")
def step_json_serialisable(context):
    s = json.dumps(context.result_summary)
    assert len(s) > 0


@then('result_summary.summary has key "{key}"')
def step_summary_has_key(context, key):
    summary = context.result_summary.get("summary") or {}
    assert key in summary, f"missing {key!r}; have {sorted(summary)}"


@then("result_summary.strategies has {n:d} entry")
@then("result_summary.strategies has {n:d} entries")
def step_strategies_count(context, n):
    strategies = context.result_summary.get("strategies") or []
    assert len(strategies) == n, f"expected {n} strategies, got {len(strategies)}"


@then("the first strategy entry exposes keys decisions, bars_seen, recent_fills, positions")
def step_strategy_keys(context):
    s = context.result_summary["strategies"][0]
    for key in ("decisions", "bars_seen", "recent_fills", "positions"):
        assert key in s, f"missing {key} in strategy entry"


@then('a SystemExit is raised mentioning "{token}"')
def step_systemexit(context, token):
    assert isinstance(context.error, SystemExit), (
        f"expected SystemExit, got {type(context.error).__name__}"
    )
    assert token in str(context.error.code or context.error), (
        f"expected {token!r} in error, got {context.error!r}"
    )
