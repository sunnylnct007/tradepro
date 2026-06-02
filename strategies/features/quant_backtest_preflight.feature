Feature: Quant-backtest preflight (Phase D-3 / E migration)
  tradepro-quant-backtest now threads BarStore.preflight_data_state
  through every symbol, combines per-symbol hashes into one
  fingerprint, and refuses incomplete coverage unless the operator
  opts in.

  Scenario: combined hash is deterministic and order-independent
    Given per-symbol hashes
      | symbol | hash      |
      | SPY    | aaa11111  |
      | QQQ    | bbb22222  |
      | IWM    | ccc33333  |
    When the combined hash is computed
    Then the combined hash equals the SHA256 of the sorted lines
    And reordering the input symbols produces the same combined hash

  Scenario: empty input maps to the sentinel
    Given per-symbol hashes
      | symbol | hash |
    When the combined hash is computed
    Then the combined hash equals "EMPTY_DATA_STATE"

  Scenario: one symbol's hash change shifts the combined fingerprint
    Given per-symbol hashes
      | symbol | hash      |
      | SPY    | aaa11111  |
      | QQQ    | bbb22222  |
    When the combined hash is computed
    And SPY's hash becomes "ddd99999"
    And the combined hash is computed again
    Then the two combined hashes differ

  Scenario: preflight aggregates per-symbol BarStore states
    Given a fake preflight that returns
      | symbol | hash      | coverage_complete |
      | SPY    | aaa11111  | true              |
      | QQQ    | bbb22222  | true              |
    When the quant preflight runs for asset_class "us_etf" resolution "1d"
    Then the per_symbol_states block carries 2 entries
    And the combined data_state_hash is non-empty
    And coverage_complete is true at the top level

  Scenario: one incomplete symbol marks the whole run incomplete
    Given a fake preflight that returns
      | symbol | hash      | coverage_complete |
      | SPY    | aaa11111  | true              |
      | QQQ    | bbb22222  | false             |
    When the quant preflight runs for asset_class "us_etf" resolution "1d" allowing incomplete data
    Then coverage_complete is false at the top level
    And the combined data_state_hash is still produced

  Scenario: equity-pipeline preflight documents the hibeta exclusion
    Given a fake preflight that returns
      | symbol | hash      | coverage_complete |
      | SPY    | aaa11111  | true              |
      | AAPL   | bbb22222  | true              |
      | GLD    | ccc33333  | true              |
    When the equity-pipeline preflight runs for asset_class "us_etf" resolution "1d"
    Then the per_symbol_states block carries 3 entries
    And the preflight_scope lists "benchmark", "large_50", and "gold"
    And the excluded_from_hash list mentions hibeta
