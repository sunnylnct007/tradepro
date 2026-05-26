Feature: Viz chart framework — registry + Plotly JSON contract

  Trader-requested visualisations and any future charts plug into a
  single registry. Each builder returns a Plotly figure JSON dict
  the frontend renders unchanged. This feature pins the registry
  contract + smoke-tests the two anchor charts (backtest_4panel,
  monte_carlo_fan) actually produce valid Plotly JSON.

  Scenario: Registry exposes the two trader-anchor charts
    Given the viz registry is loaded
    Then chart "backtest_4panel" is registered
    And chart "monte_carlo_fan" is registered
    And each registered chart declares a non-empty description

  Scenario: Building an unknown chart raises a helpful KeyError
    Given the viz registry is loaded
    When I try to build chart "no_such_chart_42"
    Then a KeyError is raised mentioning the available chart names

  Scenario: backtest_4panel produces a Plotly figure with four subplots
    Given the viz registry is loaded
    And a synthetic EnsembleResult covering 100 trading days with two sleeves
    And a synthetic SPY benchmark series
    When I build chart "backtest_4panel"
    Then the figure JSON has a "data" key with at least 5 traces
    And the figure JSON has a "layout" key with non-empty annotations
    And the figure JSON round-trips through json.dumps without error

  Scenario: monte_carlo_fan produces a fan + histogram figure
    Given the viz registry is loaded
    And a synthetic MonteCarloResult with 200 paths over 5 years
    When I build chart "monte_carlo_fan"
    Then the figure JSON has a "data" key with at least 5 traces
    And the figure JSON has a histogram trace among its data
    And the figure JSON round-trips through json.dumps without error
