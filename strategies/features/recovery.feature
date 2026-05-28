Feature: Drawdown recovery time alongside max-DD
  Max drawdown is half the story. A −30% drawdown that recovered in
  9 months is a very different signal from a −30% drawdown that took
  7 years to come back, and an even more different signal from one
  that hasn't recovered yet at all. Pin both pieces of the metric.

  Scenario: A drawdown that fully recovered surfaces a recovery_days
    Given a synthetic equity curve that drew down 25% and reclaimed the prior peak after 140 days
    When I compute the backtest stats
    Then the max-DD is approximately -25%
    And max_drawdown_recovery_days is approximately 140
    And max_drawdown_still_recovering is False

  Scenario: A drawdown still in progress reports it as still recovering
    Given a synthetic equity curve that drew down 33% and never reclaimed the prior peak
    When I compute the backtest stats
    Then max_drawdown_recovery_days is null
    And max_drawdown_still_recovering is True
    And days_since_max_dd_trough is at least 0
