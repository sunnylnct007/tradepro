Feature: 3-axis strategy taxonomy — horizon + strategy_type
  Every strategy declares a horizon (intraday / swing / medium_term /
  long_term) and a strategy_type (momentum / mean_reversion / …) in
  STRATEGY_TAXONOMY. The compare pipeline decorates every row with
  these fields so the UI can render "show me only swing momentum
  signals" without re-deriving from the strategy name.

  Per IMPROVEMENT_SUGGESTIONS_v1.md §1.1 + §1.2.

  Scenario Outline: each in-tree strategy has a registered horizon + type
    Given the strategy named "<name>"
    When I look up its taxonomy
    Then the horizon is "<horizon>"
    And the strategy_type is "<type>"

    Examples:
      | name                 | horizon     | type           |
      | buy_and_hold         | long_term   | momentum       |
      | sma_crossover        | medium_term | momentum       |
      | macd_signal_cross    | swing       | momentum       |
      | donchian_breakout    | swing       | momentum       |
      | ichimoku_cloud       | swing       | momentum       |
      | rsi_mean_reversion   | swing       | mean_reversion |
      | bollinger_bounce     | swing       | mean_reversion |

  Scenario: unknown strategy returns None for both axes
    Given the strategy named "made_up_strategy_xyz"
    When I look up its taxonomy
    Then the horizon is None
    And the strategy_type is None
