Feature: Multi-strategy bucket vote (BUY / WAIT / AVOID)
  The Compare page and the MCP `evaluate_symbols` tool both fold a
  symbol's now-or-wait verdict + per-strategy long/flat votes into
  one of three buckets. Pin the rules so a future refactor doesn't
  silently demote a BUY into a WAIT (or vice versa).

  Scenario: AVOID overrides everything
    Given price verdict AVOID with reason "below 200-day SMA"
    And 5 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is AVOID
    And the reason mentions "below 200-day SMA"

  Scenario: WAIT from price action overrides majority long
    Given price verdict WAIT with reason "RSI overbought"
    And 4 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is WAIT
    And the reason mentions "RSI overbought"

  Scenario: BUY when price says go and majority of strategies are long
    Given price verdict BUY with reason "above 200-day SMA"
    And 4 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is BUY

  Scenario: HOLD with majority long becomes BUY (price reason wins when supplied)
    Given price verdict HOLD with reason "neutral"
    And 3 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is BUY
    And the reason mentions "neutral"

  Scenario: HOLD with majority long and no price reason — count surfaces
    Given price verdict HOLD with no reason
    And 3 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is BUY
    And the reason mentions "3 of 5 strategies currently long"

  Scenario: HOLD with minority long becomes WAIT
    Given price verdict HOLD with reason "neutral"
    And 2 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is WAIT
    And the reason mentions "Only 2 of 5"

  Scenario: Single-strategy long with HOLD price verdict still WAIT
    Given price verdict HOLD with reason "neutral"
    And 1 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is WAIT
