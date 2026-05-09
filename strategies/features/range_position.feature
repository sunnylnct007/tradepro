Feature: Range-position guard on BUY signals
  A symbol can pass the trend / RSI / drawdown gates yet still be
  sitting near the top of its 52-week range — risk/reward for a fresh
  entry is asymmetric (small upside to the high, large downside to the
  low). The classifier downgrades BUY → HOLD when the price is at or
  above the 70th percentile of the 52w range. The VUKE-class fix:
  prevents "5% off 52w high after a +24% YoY run" from being labelled
  a swing entry.

  Scenario: synthetic uptrend ending near the 52w high → HOLD, not BUY
    Given a synthetic VUKE-shaped price series ending at the 70th+ percentile of its 52w range
    When I compute the market state
    Then the entry signal is "HOLD"
    And the entry reason mentions "percentile of 52w range"
    And the decision trace contains a "Range position (52w)" row with status "fail"

  Scenario: symbol mid-range with healthy uptrend stays BUY
    Given a synthetic price series ending at the 50th percentile of its 52w range
    When I compute the market state
    Then the decision trace contains a "Range position (52w)" row with status "warn"

  Scenario: symbol near the 52w low passes the range-position gate as a dip
    Given a synthetic recovering price series ending at the 30th percentile of its 52w range
    When I compute the market state
    Then the decision trace contains a "Range position (52w)" row with status "pass"
