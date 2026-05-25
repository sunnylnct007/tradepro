Feature: Strategy provenance + lifecycle metadata
  Every strategy carries three ClassVars — source (provenance), status
  (where it sits in the evaluate → backtest → scheduled → live pipeline),
  and default_lookback_days (historical-bar hint). The daemon reads
  these so a new trader-shipped strategy works without daemon edits.
  Pin the contract so a future refactor can't silently strip the
  metadata and reintroduce the FX-warmup blocker we just fixed.

  Scenario: every registered strategy declares the three metadata fields
    Given the paper strategies package is imported
    Then every registered strategy class declares source
    And every registered strategy class declares status
    And every registered strategy class declares default_lookback_days

  Scenario: ichimoku_fx_mr carries trader-quant + 200-day lookback
    Given the paper strategies package is imported
    When I look up the "ichimoku_fx_mr" strategy class
    Then its source is "trader-quant"
    And its default_lookback_days is 200

  Scenario: ichimoku_equity is trader-quant with no lookback
    Given the paper strategies package is imported
    When I look up the "ichimoku_equity" strategy class
    Then its source is "trader-quant"
    And its default_lookback_days is 0

  Scenario: compass_momentum is alpha-engine
    Given the paper strategies package is imported
    When I look up the "compass_momentum" strategy class
    Then its source is "alpha-engine"

  Scenario: scaffold strategies inherit the default source
    Given the paper strategies package is imported
    When I look up the "ma_crossover" strategy class
    Then its source is "scaffold"

  Scenario: catalog push payload surfaces metadata
    Given the paper strategies package is imported
    When I build the catalog payload
    Then every catalog entry carries source, status, default_lookback_days
    And the ichimoku_fx_mr entry has source "trader-quant" and default_lookback_days 200

  Scenario: daemon resolves default_lookback_days from the registry
    Given trigger params with strategy "ichimoku_fx_mr" and lookback_days 0
    When the daemon parses the params
    Then the resolved lookback_days is 200

  Scenario: daemon does not override a user-supplied lookback
    Given trigger params with strategy "ichimoku_fx_mr" and lookback_days 50
    When the daemon parses the params
    Then the resolved lookback_days is 50

  Scenario: daemon leaves lookback at 0 for strategies that don't need history
    Given trigger params with strategy "ma_crossover" and lookback_days 0
    When the daemon parses the params
    Then the resolved lookback_days is 0
