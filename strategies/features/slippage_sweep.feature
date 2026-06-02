Feature: Slippage sensitivity sweep (Phase F-1)
  Re-run a backtest at multiple slippage_bps rungs and surface the
  break-even bps + a robust/fragile/broken verdict — answers L2:
  "would my strategy survive at a wider spread?"

  Scenario: monotonic-decreasing P&L crosses zero between two rungs
    Given a sweep result with rungs
      | bps  | pnl    |
      | 1.0  | 200.0  |
      | 5.0  | 100.0  |
      | 10.0 | -50.0  |
      | 25.0 | -300.0 |
    When break_even_bps is computed
    Then break_even_bps is between 5.0 and 10.0
    And the sweep verdict is "fragile"

  Scenario: positive P&L at every rung — robust
    Given a sweep result with rungs
      | bps  | pnl    |
      | 1.0  | 500.0  |
      | 5.0  | 450.0  |
      | 10.0 | 400.0  |
      | 25.0 | 200.0  |
      | 50.0 | 50.0   |
    When break_even_bps is computed
    Then break_even_bps is None
    And the sweep verdict is "robust"

  Scenario: negative P&L at every rung — broken
    Given a sweep result with rungs
      | bps  | pnl    |
      | 1.0  | -10.0  |
      | 5.0  | -50.0  |
      | 10.0 | -100.0 |
    When break_even_bps is computed
    Then break_even_bps is None
    And the sweep verdict is "broken"

  Scenario: zero-fill sweep classifies as broken
    Given a sweep result with all zero fills
    When the verdict is computed
    Then the sweep verdict is "broken"

  Scenario: end-to-end sweep runs the validator once per bps level
    Given a fake validator that returns pnl = 100 - 5 * bps
    When the sweep runs across bps [1, 5, 10, 25, 50]
    Then 5 rows are produced in ascending bps order
    And the validator was invoked 5 times
    And the sweep verdict is "fragile"
    And break_even_bps is approximately 20.0
