"""BDD steps for quant_engine.feature.

All fixtures use synthetic data — no network calls, no yfinance.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from behave import given, then, when

from tradepro_strategies.quant_engine.portfolio_metrics import (
    max_drawdown,
    sharpe,
    summarise,
)
from tradepro_strategies.quant_engine.vol_targeting import (
    apply_vol_target,
    vol_target_scalar,
)
from tradepro_strategies.quant_engine.regime_filter import RegimeFilter
from tradepro_strategies.quant_engine.walk_forward import WalkForwardValidator
from tradepro_strategies.quant_engine.monte_carlo import MonteCarloSimulator
from tradepro_strategies.quant_engine.sleeve import Sleeve
from tradepro_strategies.quant_engine.fx_strategy import FXMeanReversionStrategy


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_returns(n: int, mu: float = 0.0, sigma: float = 0.01,
                  seed: int = 42) -> pd.Series:
    """Synthetic daily returns with a DatetimeIndex (business days)."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2018-01-01", periods=n, freq="B")
    values = rng.normal(mu, sigma, n)
    return pd.Series(values, index=dates, name="returns")


def _make_equity(returns: pd.Series, initial: float = 100_000.0) -> pd.Series:
    """Equity curve from a returns series."""
    return (1.0 + returns).cumprod() * initial


def _make_ohlc(n: int, seed: int = 42, start_price: float = 100.0) -> pd.DataFrame:
    """Synthetic daily OHLC DataFrame with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2018-01-01", periods=n, freq="B")
    log_returns = rng.normal(0.0005, 0.015, n)
    closes = start_price * np.exp(np.cumsum(log_returns))
    noise_h = np.abs(rng.normal(0, 0.005, n))
    noise_l = np.abs(rng.normal(0, 0.005, n))
    highs = closes * (1 + noise_h)
    lows = closes * (1 - noise_l)
    opens = np.roll(closes, 1)
    opens[0] = start_price
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": rng.integers(100_000, 10_000_000, n).astype(float),
    }, index=dates)


def _make_hourly_ohlc(n: int, seed: int = 42, start_price: float = 1.10) -> pd.DataFrame:
    """Synthetic hourly OHLC DataFrame with DatetimeIndex for FX."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="h")
    log_returns = rng.normal(0.0, 0.0008, n)
    closes = start_price * np.exp(np.cumsum(log_returns))
    noise_h = np.abs(rng.normal(0, 0.0003, n))
    noise_l = np.abs(rng.normal(0, 0.0003, n))
    highs = closes * (1 + noise_h)
    lows = closes * (1 - noise_l)
    opens = np.roll(closes, 1)
    opens[0] = start_price
    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": rng.integers(1_000, 100_000, n).astype(float),
    }, index=dates)


# ---------------------------------------------------------------------------
# Portfolio Metrics — Given
# ---------------------------------------------------------------------------

@given("a returns series of {n:d} bars all equal to 0.0")
def step_zero_returns(context, n: int) -> None:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    context.returns = pd.Series(0.0, index=dates, name="returns")


@given("a returns series of {n:d} bars with mu={mu:f} and sigma={sigma:f} and seed={seed:d}")
def step_returns_mu_sigma_seed(context, n: int, mu: float, sigma: float, seed: int) -> None:
    context.returns = _make_returns(n, mu=mu, sigma=sigma, seed=seed)


@given("a monotone-increasing equity curve of {n:d} bars")
def step_monotone_equity(context, n: int) -> None:
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    context.equity = pd.Series(
        [100_000.0 * (1 + i * 0.001) for i in range(n)],
        index=dates,
    )


@given("an equity curve with a 20% drawdown")
def step_equity_20_pct_drawdown(context) -> None:
    # Peak at 120, trough at 96 → dd = (96-120)/120 = -0.2
    vals = [100.0, 105.0, 110.0, 115.0, 120.0, 108.0, 96.0, 100.0, 102.0]
    dates = pd.date_range("2020-01-01", periods=len(vals), freq="B")
    context.equity = pd.Series(vals, index=dates)


@given("the corresponding equity curve")
def step_corresponding_equity(context) -> None:
    context.equity = _make_equity(context.returns)


# ---------------------------------------------------------------------------
# Portfolio Metrics — When
# ---------------------------------------------------------------------------

@when("I compute the sharpe ratio")
def step_compute_sharpe(context) -> None:
    context.result = sharpe(context.returns)


@when("I compute the max drawdown")
def step_compute_max_drawdown(context) -> None:
    context.result = max_drawdown(context.equity)


@when("I call summarise on equity and returns")
def step_call_summarise(context) -> None:
    context.summary = summarise(context.equity, context.returns)


# ---------------------------------------------------------------------------
# Portfolio Metrics — Then
# ---------------------------------------------------------------------------

@then("the result is {expected:f}")
def step_result_equals(context, expected: float) -> None:
    assert context.result == expected, f"expected {expected}, got {context.result}"


@then("the result is approximately {expected:f} with tolerance {tol:f}")
def step_result_approx(context, expected: float, tol: float) -> None:
    assert abs(context.result - expected) <= tol, (
        f"expected approx {expected} ± {tol}, got {context.result}"
    )


@then("the summary has keys cagr_pct, sharpe, sortino, max_drawdown_pct, calmar, omega")
def step_summary_keys(context) -> None:
    required = {"cagr_pct", "sharpe", "sortino", "max_drawdown_pct", "calmar", "omega"}
    missing = required - set(context.summary.keys())
    assert not missing, f"summary missing keys: {missing}"


# ---------------------------------------------------------------------------
# Vol Targeting — Given
# ---------------------------------------------------------------------------

@given("a returns series of {n:d} bars with 24% annual vol and seed={seed:d}")
def step_returns_24_pct_vol(context, n: int, seed: int) -> None:
    target_daily_sigma = 0.24 / math.sqrt(252)
    context.returns = _make_returns(n, mu=0.0, sigma=target_daily_sigma, seed=seed)


@given("a returns series of {n:d} bars with near-zero vol and seed={seed:d}")
def step_returns_near_zero_vol(context, n: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    values = rng.normal(0.0, 1e-8, n)  # essentially zero vol
    context.returns = pd.Series(values, index=dates, name="returns")


# ---------------------------------------------------------------------------
# Vol Targeting — When
# ---------------------------------------------------------------------------

@when("I apply vol targeting with target={target:f} and max_leverage={max_lev:f} and lookback={lb:d}")
def step_apply_vol_target(context, target: float, max_lev: float, lb: int) -> None:
    context.scaled_returns, context.vol_scalar = apply_vol_target(
        context.returns,
        target_vol=target,
        max_leverage=max_lev,
        lookback=lb,
    )


@when("I compute the vol target scalar with lookback={lb:d}")
def step_compute_vol_scalar(context, lb: int) -> None:
    context.vol_scalar = vol_target_scalar(context.returns, lookback=lb)


# ---------------------------------------------------------------------------
# Vol Targeting — Then
# ---------------------------------------------------------------------------

@then("the realised annual vol of scaled returns after warmup is approximately {expected:f} with tolerance {tol:f}")
def step_realised_vol_approx(context, expected: float, tol: float) -> None:
    # Skip warmup period (first 60 bars)
    warmup = 60
    after_warmup = context.scaled_returns.iloc[warmup:]
    realised = after_warmup.std() * math.sqrt(252)
    assert abs(realised - expected) <= tol, (
        f"expected realised vol ≈ {expected} ± {tol}, got {realised:.4f}"
    )


@then("the maximum scalar value is at most {cap:f}")
def step_scalar_cap(context, cap: float) -> None:
    max_val = context.vol_scalar.max()
    assert max_val <= cap + 1e-9, f"expected scalar ≤ {cap}, got max {max_val}"


@then("the scalar at index 0 is 1.0 because no prior data exists")
def step_scalar_first_bar(context) -> None:
    first = float(context.vol_scalar.iloc[0])
    assert first == 1.0, f"expected scalar[0] == 1.0 (fillna), got {first}"


# ---------------------------------------------------------------------------
# Regime Filter — Given
# ---------------------------------------------------------------------------

@given("a SPY close series of {n:d} bars trending up")
def step_spy_uptrend(context, n: int) -> None:
    dates = pd.date_range("2017-01-01", periods=n, freq="B")
    closes = pd.Series(
        [100.0 * (1.002 ** i) for i in range(n)], index=dates
    )
    context.spy_close = closes
    context.n_bars = n


@given("a SPY close series that drops below SMA on the last 30 bars")
def step_spy_drop_below_sma(context) -> None:
    n = 300
    dates = pd.date_range("2017-01-01", periods=n, freq="B")
    closes_vals = [200.0] * (n - 30) + [100.0] * 30  # sharp drop below SMA
    context.spy_close = pd.Series(closes_vals, index=dates)
    context.n_bars = n


@given("a SPY close series with bear regime for the last 50 bars")
def step_spy_bear_last_50(context) -> None:
    n = 300
    dates = pd.date_range("2017-01-01", periods=n, freq="B")
    closes_vals = [200.0] * (n - 50) + [50.0] * 50  # drop to 50 (well below SMA)
    context.spy_close = pd.Series(closes_vals, index=dates)
    context.n_bars = n
    # Pre-build the regime filter (used later by gate_signals step)
    context.regime_filter = RegimeFilter(context.spy_close, sma_period=200)


@given("a signals DataFrame with {n:d} rows all equal to 1.0")
def step_signals_all_ones(context, n: int) -> None:
    dates = pd.date_range("2017-01-01", periods=n, freq="B")
    context.signals_df = pd.DataFrame({"A": 1.0, "B": 1.0}, index=dates)


# ---------------------------------------------------------------------------
# Regime Filter — When
# ---------------------------------------------------------------------------

@when("I build a RegimeFilter with sma_period={sma_period:d}")
def step_build_regime_filter(context, sma_period: int) -> None:
    context.regime_filter = RegimeFilter(context.spy_close, sma_period=sma_period)


@when("I call gate_signals on the signals")
def step_call_gate_signals(context) -> None:
    context.gated_signals = context.regime_filter.gate_signals(context.signals_df)


# ---------------------------------------------------------------------------
# Regime Filter — Then
# ---------------------------------------------------------------------------

@then("the last bar is_bull is {expected}")
def step_is_bull(context, expected: str) -> None:
    expected_bool = expected.strip().lower() == "true"
    last_date = context.spy_close.index[-1]
    result = context.regime_filter.is_bull(last_date)
    assert result == expected_bool, (
        f"expected is_bull({last_date.date()}) == {expected_bool}, got {result}"
    )


@then("the last {n:d} rows of gated signals are all 0.0")
def step_gated_last_n_zero(context, n: int) -> None:
    tail = context.gated_signals.iloc[-n:]
    assert (tail == 0.0).all().all(), (
        f"expected last {n} rows to be 0.0; got\n{tail.to_string()}"
    )


# ---------------------------------------------------------------------------
# Walk-Forward — Given
# ---------------------------------------------------------------------------

@given("a daily returns series from {start} to {end}")
def step_daily_returns_range(context, start: str, end: str) -> None:
    dates = pd.date_range(start, end, freq="B")
    rng = np.random.default_rng(42)
    values = rng.normal(0.0003, 0.01, len(dates))
    context.returns = pd.Series(values, index=dates, name="returns")


# ---------------------------------------------------------------------------
# Walk-Forward — When
# ---------------------------------------------------------------------------

@when("I run WalkForwardValidator with defaults")
def step_run_wfv_defaults(context) -> None:
    validator = WalkForwardValidator(context.returns)
    context.oos_returns, context.wf_windows = validator.run()


@when("I run WalkForwardValidator with max_leverage={max_lev:f}")
def step_run_wfv_max_lev(context, max_lev: float) -> None:
    validator = WalkForwardValidator(context.returns, max_leverage=max_lev)
    context.oos_returns, context.wf_windows = validator.run()


# ---------------------------------------------------------------------------
# Walk-Forward — Then
# ---------------------------------------------------------------------------

@then("there are {n:d} WalkForwardWindow results")
def step_n_wf_windows(context, n: int) -> None:
    assert len(context.wf_windows) == n, (
        f"expected {n} windows, got {len(context.wf_windows)}"
    )


@then("every window vol_scalar is at most {cap:f}")
def step_all_scalars_le(context, cap: float) -> None:
    for w in context.wf_windows:
        assert w.vol_scalar <= cap + 1e-9, (
            f"window {w.test_year} scalar {w.vol_scalar} > {cap}"
        )


@then("the OOS returns index covers years {y1:d}, {y2:d}, {y3:d}, {y4:d}, {y5:d}")
def step_oos_years(context, y1: int, y2: int, y3: int, y4: int, y5: int) -> None:
    years_in_oos = set(context.oos_returns.index.year.unique())
    expected = {y1, y2, y3, y4, y5}
    assert expected.issubset(years_in_oos), (
        f"expected years {expected} in OOS index; found {sorted(years_in_oos)}"
    )


# ---------------------------------------------------------------------------
# Monte Carlo — Given (reuses step_returns_mu_sigma_seed from above)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Monte Carlo — When
# ---------------------------------------------------------------------------

@when("I run MonteCarloSimulator with n_sims={n_sims:d} and years={years:d}")
def step_run_mc(context, n_sims: int, years: int) -> None:
    mc = MonteCarloSimulator(context.returns, seed=42)
    context.mc_result = mc.run(years=years, n_sims=n_sims)


@when("I run MonteCarloSimulator twice with seed={seed:d} n_sims={n_sims:d} and years={years:d}")
def step_run_mc_twice(context, seed: int, n_sims: int, years: int) -> None:
    mc1 = MonteCarloSimulator(context.returns, seed=seed)
    mc2 = MonteCarloSimulator(context.returns, seed=seed)
    context.mc_result_1 = mc1.run(years=years, n_sims=n_sims)
    context.mc_result_2 = mc2.run(years=years, n_sims=n_sims)


# ---------------------------------------------------------------------------
# Monte Carlo — Then
# ---------------------------------------------------------------------------

@then("the paths array shape is ({n_sims:d}, {n_days:d})")
def step_paths_shape(context, n_sims: int, n_days: int) -> None:
    shape = context.mc_result.paths.shape
    assert shape == (n_sims, n_days), (
        f"expected paths shape ({n_sims}, {n_days}), got {shape}"
    )


@then("both runs produce identical final values")
def step_mc_identical(context) -> None:
    fv1 = context.mc_result_1.paths[:, -1]
    fv2 = context.mc_result_2.paths[:, -1]
    np.testing.assert_array_equal(fv1, fv2, err_msg="MC final values differ between runs with same seed")


@then("the summary has percentile keys p5, p50, p95")
def step_mc_percentile_keys(context) -> None:
    pcts = context.mc_result.summary.get("percentiles", {})
    for key in ["p5", "p50", "p95"]:
        assert key in pcts, f"summary.percentiles missing key '{key}'"


@then("the summary has probability keys p_lose_money and p_double")
def step_mc_prob_keys(context) -> None:
    s = context.mc_result.summary
    assert "p_lose_money" in s, "summary missing 'p_lose_money'"
    assert "p_double" in s, "summary missing 'p_double'"


# ---------------------------------------------------------------------------
# Sleeve — Given
# ---------------------------------------------------------------------------

@given("{n:d} tickers each with {bars:d} bars of OHLC data and seed={seed:d}")
def step_n_tickers_ohlc(context, n: int, bars: int, seed: int) -> None:
    context.ticker_data = {
        f"TKR{i}": _make_ohlc(bars, seed=seed + i * 17)
        for i in range(n)
    }
    context.ticker_bars = bars
    context.regime_filter_obj = None  # default: no regime filter


@given("a bear-only RegimeFilter")
def step_bear_only_regime(context) -> None:
    # Build a SPY close that stays below its SMA for the entire test period
    # Use the same date range as the ticker data
    first_df = next(iter(context.ticker_data.values()))
    n = len(first_df)
    dates = first_df.index
    # Flat then sharp drop: SMA will be high, close will be low
    closes_vals = [200.0] * max(200, n // 2) + [50.0] * n
    closes_vals = closes_vals[:max(n, 300)]
    dates_ext = pd.date_range(dates[0] - pd.offsets.BDay(200), periods=len(closes_vals), freq="B")
    spy_close = pd.Series(closes_vals, index=dates_ext)
    # Extend to cover dates
    spy_close = spy_close.reindex(
        pd.date_range(dates_ext[0], dates[-1], freq="B")
    ).ffill().fillna(50.0)
    context.regime_filter_obj = RegimeFilter(spy_close, sma_period=200)


# ---------------------------------------------------------------------------
# Sleeve — When
# ---------------------------------------------------------------------------

@when('I run a Sleeve named "{name}"')
def step_run_sleeve(context, name: str) -> None:
    sleeve = Sleeve(name=name, data=context.ticker_data)
    context.sleeve_result = sleeve.run()


@when('I run a Sleeve named "{name}" with the regime filter')
def step_run_sleeve_with_regime(context, name: str) -> None:
    sleeve = Sleeve(
        name=name,
        data=context.ticker_data,
        regime=context.regime_filter_obj,
    )
    context.sleeve_result = sleeve.run()


# ---------------------------------------------------------------------------
# Sleeve — Then
# ---------------------------------------------------------------------------

@then("the sleeve returns series has more than {n:d} entries")
def step_sleeve_returns_len(context, n: int) -> None:
    length = len(context.sleeve_result.returns)
    assert length > n, f"expected > {n} entries in sleeve returns, got {length}"


@then("the sleeve returns series has no NaN values")
def step_sleeve_no_nan(context) -> None:
    n_nan = context.sleeve_result.returns.isna().sum()
    assert n_nan == 0, f"sleeve returns has {n_nan} NaN values"


@then("all sleeve returns are zero")
def step_sleeve_all_zero(context) -> None:
    r = context.sleeve_result.returns
    nonzero = (r != 0.0).sum()
    assert nonzero == 0, (
        f"expected all sleeve returns to be 0.0 under bear regime; "
        f"found {nonzero} non-zero values"
    )


# ---------------------------------------------------------------------------
# FX Strategy — Given
# ---------------------------------------------------------------------------

@given("{n_pairs:d} FX pairs each with {bars:d} hourly bars of OHLC data and seed={seed:d}")
def step_fx_pair_data(context, n_pairs: int, bars: int, seed: int) -> None:
    pairs = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"][:n_pairs]
    context.fx_pair_data = {
        pair: _make_hourly_ohlc(bars, seed=seed + i * 13, start_price=1.10 + i * 0.1)
        for i, pair in enumerate(pairs)
    }


# ---------------------------------------------------------------------------
# FX Strategy — When
# ---------------------------------------------------------------------------

@when("I run FXMeanReversionStrategy")
def step_run_fx_strategy(context) -> None:
    strategy = FXMeanReversionStrategy(horizon=336, smooth=24)
    context.fx_result = strategy.run(context.fx_pair_data)


# ---------------------------------------------------------------------------
# FX Strategy — Then
# ---------------------------------------------------------------------------

@then("per_pair_stats has {n:d} entries")
def step_fx_per_pair_stats_len(context, n: int) -> None:
    assert len(context.fx_result.per_pair_stats) == n, (
        f"expected {n} per_pair_stats entries, "
        f"got {len(context.fx_result.per_pair_stats)}"
    )


@then("each pair PnL series has more than {n:d} bars")
def step_fx_pnl_len(context, n: int) -> None:
    for pair, pnl in context.fx_result.pair_pnls.items():
        assert len(pnl) > n, (
            f"pair {pair} PnL has {len(pnl)} bars, expected > {n}"
        )


@then("portfolio_pnl equals the mean of pair_pnls to 8 decimal places")
def step_fx_portfolio_mean(context) -> None:
    pnl_df = pd.DataFrame(context.fx_result.pair_pnls).fillna(0.0)
    expected = pnl_df.mean(axis=1)
    actual = context.fx_result.portfolio_pnl.fillna(0.0)
    # Align indices
    common = expected.index.intersection(actual.index)
    np.testing.assert_array_almost_equal(
        actual.loc[common].values,
        expected.loc[common].values,
        decimal=8,
        err_msg="portfolio_pnl does not equal mean of pair_pnls",
    )


@then("portfolio_stats has keys sharpe, sortino, cagr_pct, max_drawdown_pct, calmar")
def step_fx_portfolio_stats_keys(context) -> None:
    required = {"sharpe", "sortino", "cagr_pct", "max_drawdown_pct", "calmar"}
    missing = required - set(context.fx_result.portfolio_stats.keys())
    assert not missing, f"portfolio_stats missing keys: {missing}"
