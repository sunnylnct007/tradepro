Feature: Quant backtest CLI — end-to-end happy path
  The worker CLI for /api/quant/backtest/run takes a JSON payload,
  runs the Ensemble + MonteCarloSimulator, builds the two trader-anchor
  charts via the viz framework, and emits a JSON-serialisable
  result_summary the daemon can post back to the API.

  All scenarios use a stubbed data loader so we never hit yfinance.

  Background:
    Given a stubbed data loader returning 200 synthetic daily bars per symbol

  Scenario: CLI produces a result_summary with both anchor charts
    When I run quant_backtest with payload symbols=["AAA","BBB"] and 100 monte-carlo sims
    Then the result_summary has kind "backtest"
    And the result_summary.charts contains key "backtest_4panel"
    And the result_summary.charts contains key "monte_carlo_fan"
    And the result_summary is JSON-serialisable

  Scenario: result_summary embeds an ensemble + monte-carlo summary block
    When I run quant_backtest with payload symbols=["AAA","BBB"] and 100 monte-carlo sims
    Then result_summary.summary has key "ensemble_summary"
    And result_summary.summary has key "monte_carlo_summary"
    And result_summary.summary has key "final_equity"

  Scenario: strategies block mirrors paper-session shape so Session Detail tabs render
    When I run quant_backtest with payload symbols=["AAA","BBB"] and 100 monte-carlo sims
    Then result_summary.strategies has 1 entry
    And the first strategy entry exposes keys decisions, bars_seen, recent_fills, positions

  Scenario: missing symbols raises a clear SystemExit
    When I run quant_backtest with payload symbols=[] and 100 monte-carlo sims
    Then a SystemExit is raised mentioning "symbols"
