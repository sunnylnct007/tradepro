Feature: Quant Engine — sleeve portfolio, vol targeting, walk-forward, Monte Carlo + FX
  All scenarios use synthetic data. No network calls. No yfinance.

  # ---- Portfolio Metrics -----------------------------------------------

  Scenario: Sharpe of all-zeros returns is 0.0
    Given a returns series of 252 bars all equal to 0.0
    When I compute the sharpe ratio
    Then the result is 0.0

  Scenario: Sharpe on known μ=0.001 σ≈0.01 daily returns is approximately 1.5
    Given a returns series of 500 bars with mu=0.001 and sigma=0.01 and seed=42
    When I compute the sharpe ratio
    Then the result is approximately 1.5 with tolerance 0.5

  Scenario: Max drawdown on monotone-increasing equity is 0.0
    Given a monotone-increasing equity curve of 200 bars
    When I compute the max drawdown
    Then the result is 0.0

  Scenario: Max drawdown on equity that falls 20% from peak is approximately -0.20
    Given an equity curve with a 20% drawdown
    When I compute the max drawdown
    Then the result is approximately -0.20 with tolerance 0.01

  Scenario: summarise returns all required metric keys
    Given a returns series of 252 bars with mu=0.0005 and sigma=0.01 and seed=1
    And the corresponding equity curve
    When I call summarise on equity and returns
    Then the summary has keys cagr_pct, sharpe, sortino, max_drawdown_pct, calmar, omega

  # ---- Vol Targeting ---------------------------------------------------

  Scenario: Returns with 24% annual vol scaled to 12% target achieves approximately 12% vol
    Given a returns series of 500 bars with 24% annual vol and seed=99
    When I apply vol targeting with target=0.12 and max_leverage=1.5 and lookback=60
    Then the realised annual vol of scaled returns after warmup is approximately 0.12 with tolerance 0.05

  Scenario: Vol scalar is capped at max_leverage when vol is near zero
    Given a returns series of 200 bars with near-zero vol and seed=7
    When I apply vol targeting with target=0.12 and max_leverage=1.5 and lookback=60
    Then the maximum scalar value is at most 1.5

  Scenario: Vol scalar at index t uses only data before t
    Given a returns series of 300 bars with mu=0.0 and sigma=0.01 and seed=5
    When I compute the vol target scalar with lookback=60
    Then the scalar at index 0 is 1.0 because no prior data exists

  # ---- Regime Filter ---------------------------------------------------

  Scenario: SPY trending up 300 bars produces bull regime on last bar
    Given a SPY close series of 300 bars trending up
    When I build a RegimeFilter with sma_period=200
    Then the last bar is_bull is True

  Scenario: SPY drops sharply below SMA produces bear regime on last bar
    Given a SPY close series that drops below SMA on the last 30 bars
    When I build a RegimeFilter with sma_period=200
    Then the last bar is_bull is False

  Scenario: gate_signals zeros last 50 rows when regime is bear for last 50 bars
    Given a SPY close series with bear regime for the last 50 bars
    And a signals DataFrame with 300 rows all equal to 1.0
    When I call gate_signals on the signals
    Then the last 50 rows of gated signals are all 0.0

  # ---- Walk-Forward ---------------------------------------------------

  Scenario: WalkForwardValidator with default 5 windows on 2018-2025 returns 5 results
    Given a daily returns series from 2018-01-01 to 2025-12-31
    When I run WalkForwardValidator with defaults
    Then there are 5 WalkForwardWindow results

  Scenario: Every walk-forward window vol_scalar is at most 1.5
    Given a daily returns series from 2018-01-01 to 2025-12-31
    When I run WalkForwardValidator with max_leverage=1.5
    Then every window vol_scalar is at most 1.5

  Scenario: OOS returns index covers all five test years
    Given a daily returns series from 2018-01-01 to 2025-12-31
    When I run WalkForwardValidator with defaults
    Then the OOS returns index covers years 2021, 2022, 2023, 2024, 2025

  # ---- Monte Carlo -------------------------------------------------------

  Scenario: MonteCarloSimulator n_sims=1000 years=5 produces correct paths shape
    Given a returns series of 500 bars with mu=0.0003 and sigma=0.01 and seed=42
    When I run MonteCarloSimulator with n_sims=1000 and years=5
    Then the paths array shape is (1000, 1261)

  Scenario: Two Monte Carlo runs with same seed produce identical final values
    Given a returns series of 500 bars with mu=0.0003 and sigma=0.01 and seed=42
    When I run MonteCarloSimulator twice with seed=42 n_sims=100 and years=3
    Then both runs produce identical final values

  Scenario: Monte Carlo summary has required percentile and probability keys
    Given a returns series of 500 bars with mu=0.0003 and sigma=0.01 and seed=42
    When I run MonteCarloSimulator with n_sims=200 and years=5
    Then the summary has percentile keys p5, p50, p95
    And the summary has probability keys p_lose_money and p_double

  # ---- Sleeve ----------------------------------------------------------

  Scenario: Sleeve with 3 tickers and 400 bars produces non-empty returns without NaN
    Given 3 tickers each with 400 bars of OHLC data and seed=42
    When I run a Sleeve named "test_sleeve"
    Then the sleeve returns series has more than 200 entries
    And the sleeve returns series has no NaN values

  Scenario: Sleeve with bear-only RegimeFilter produces all-zero returns
    Given 3 tickers each with 400 bars of OHLC data and seed=42
    And a bear-only RegimeFilter
    When I run a Sleeve named "bear_sleeve" with the regime filter
    Then all sleeve returns are zero

  # ---- FX Strategy -----------------------------------------------------

  Scenario: FX strategy with 2 pairs and 2000 bars produces stats for each pair
    Given 2 FX pairs each with 2000 hourly bars of OHLC data and seed=11
    When I run FXMeanReversionStrategy
    Then per_pair_stats has 2 entries
    And each pair PnL series has more than 1000 bars

  Scenario: FX portfolio PnL equals mean of pair PnLs
    Given 2 FX pairs each with 2000 hourly bars of OHLC data and seed=11
    When I run FXMeanReversionStrategy
    Then portfolio_pnl equals the mean of pair_pnls to 8 decimal places

  Scenario: FX portfolio_stats has required metric keys
    Given 2 FX pairs each with 2000 hourly bars of OHLC data and seed=11
    When I run FXMeanReversionStrategy
    Then portfolio_stats has keys sharpe, sortino, cagr_pct, max_drawdown_pct, calmar
