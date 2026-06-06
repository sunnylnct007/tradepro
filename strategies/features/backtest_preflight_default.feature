Feature: Backtest preflight defaults to ON with per-symbol auto-resolve
  Every backtest CLI used to default to the legacy cache path with
  preflight only firing when --data-asset was passed explicitly.
  After this flip, preflight runs by default and the asset class
  per symbol comes from the resolver (AAPL→us_equity, SPY→us_etf,
  EURUSD=X→fx_spot, ...). --legacy-cache is the new opt-out.

  Scenario: quant preflight auto-resolves a mixed multi-class universe
    Given a fake preflight that returns
      | symbol   | hash      | coverage_complete |
      | AAPL     | aaa11111  | true              |
      | SPY      | bbb22222  | true              |
      | EURUSD=X | ccc33333  | true              |
      | BARC.L   | ddd44444  | true              |
      | BTC-USD  | eee55555  | true              |
    When the quant preflight runs with auto-resolved asset classes at resolution "1d"
    Then the per_symbol_states block carries 5 entries
    And the resolved class for AAPL is "us_equity"
    And the resolved class for SPY is "us_etf"
    And the resolved class for EURUSD=X is "fx_spot"
    And the resolved class for BARC.L is "uk_equity"
    And the resolved class for BTC-USD is "crypto"
    And the resolved_by value is "auto-resolver"

  Scenario: explicit asset_class override stamps resolved_by=explicit
    Given a fake preflight that returns
      | symbol | hash      | coverage_complete |
      | AAPL   | aaa11111  | true              |
      | SPY    | bbb22222  | true              |
    When the quant preflight runs with explicit asset_class "us_etf" at resolution "1d"
    Then the per_symbol_states block carries 2 entries
    And the resolved class for AAPL is "us_etf"
    And the resolved class for SPY is "us_etf"
    And the resolved_by value is "explicit"
