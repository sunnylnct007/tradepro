Feature: MCP get_symbol_analysis — unified card with compare-row fusion
  Wraps build_symbol_analysis_card so an LLM can ask "give me both
  lenses for AAPL" with one tool call. Optionally folds the matching
  best-Sharpe row from /api/compare/latest into the technical block.

  All scenarios stub the API fetch so the test never hits the .NET
  service or live yfinance.

  Scenario: fundamental-only card when no universe supplied
    When I call tools.get_symbol_analysis for "AAPL" with no universe
    Then the response is ok
    And the response _source is "live://symbol_analysis/AAPL"
    And the response has primary_horizon_recommendation
    And the response payload technical block is null
    And the response payload has a fundamental block
    And the response compare_row_source is null

  Scenario: universe supplied fuses the best-Sharpe compare row into the technical block
    Given a fake compare API response with rows for "AAPL"
      | strategy        | sharpe | bucket | conviction |
      | sma_crossover   | 0.42   | WAIT   | LOW        |
      | buy_and_hold    | 0.91   | BUY    | HIGH       |
      | macd_signal     | 0.55   | BUY    | MEDIUM     |
    When I call tools.get_symbol_analysis for "AAPL" with universe "etf_test"
    Then the response is ok
    And the response payload technical bucket is "BUY"
    And the response payload technical conviction is "HIGH"
    And the response compare_row_source is "tradepro://compare/etf_test/best/AAPL"

  Scenario: symbol not in universe still returns a fundamental-only card
    Given a fake compare API response with rows for "MSFT"
      | strategy     | sharpe | bucket | conviction |
      | buy_and_hold | 0.7    | WAIT   | LOW        |
    When I call tools.get_symbol_analysis for "NVDA" with universe "etf_test"
    Then the response is ok
    And the response payload technical block is null

  Scenario: missing symbol returns an error envelope
    When I call tools.get_symbol_analysis with an empty symbol
    Then the response is not ok
    And the response _source starts with "error://"
