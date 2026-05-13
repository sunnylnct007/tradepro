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

  # Behaviour change May 2026: HOLD never elevates to BUY regardless of
  # how many strategies are still long. Earlier behaviour conflated
  # "already in position" with "good time to add" and surfaced bucket=BUY
  # on rows whose own market_state said HOLD (MTUM/VLUE/QUAL at 96-100th
  # pctile of 52w range). The bucket now mirrors the price verdict on
  # HOLD with the consensus shown as context.

  Scenario: HOLD with majority long becomes WAIT (consensus reads as "still in but no edge")
    Given price verdict HOLD with reason "no fresh entry edge"
    And 3 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is WAIT
    And the reason mentions "3 of 5 strategies currently long"
    And the reason mentions "no fresh entry edge"

  Scenario: HOLD with majority long and no price reason — count surfaces but bucket stays WAIT
    Given price verdict HOLD with no reason
    And 3 of 5 strategies currently long
    When I compute the bucket
    Then the bucket is WAIT
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
