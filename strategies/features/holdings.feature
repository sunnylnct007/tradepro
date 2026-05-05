Feature: Phase-2 holdings recommendation engine
  Per-holding action (BUY_MORE / HOLD / TRIM) combining position
  state with today's bucket and swing composite. Pin every priority
  branch so a future refactor can't quietly turn a TRIM into a
  HOLD or vice versa.

  Background:
    Given a holding worth 100 shares of MSFT bought at 412.00 USD now at 416.00 USD

  Scenario: average-down zone + thesis intact → BUY_MORE
    Given today's row says BUY with swing 6/8 STRONG_BUY
    And the row's RSI is 32 above 200d SMA
    And the holding is down 5%
    When I analyse the holding
    Then the action is "BUY_MORE"
    And the narrative mentions "average-down"
    And the new cost basis is reported

  Scenario: take-profit zone fires regardless of structural state
    Given today's row says BUY with swing 6/8 STRONG_BUY
    And the row's RSI is 72 above 200d SMA
    And the holding is up 18%
    When I analyse the holding
    Then the action is "TRIM"
    And the narrative mentions "trim"

  Scenario: structurally broken + in profit → TRIM (lock in gains)
    Given today's row says AVOID with swing 1/8 AVOID
    And the row's RSI is 50 below 200d SMA
    And the holding is up 8%
    When I analyse the holding
    Then the action is "TRIM"
    And the narrative mentions "trim"

  Scenario: structurally broken + at break-even → HOLD with caveat
    Given today's row says AVOID with swing 1/8 AVOID
    And the row's RSI is 45 below 200d SMA
    And the holding is down 1%
    When I analyse the holding
    Then the action is "HOLD"
    And the narrative mentions "structural thesis"

  Scenario: WAIT bucket on a held position → HOLD; don't add
    Given today's row says WAIT with swing 4/8 BUY
    And the row's RSI is 70 above 200d SMA
    And the holding is up 1%
    When I analyse the holding
    Then the action is "HOLD"
    And the narrative mentions "don't add"

  Scenario: position not in any tracked universe → HOLD with run-eval prompt
    Given no compare row for the holding
    When I analyse the holding
    Then the action is "HOLD"
    And the narrative mentions "evaluate_symbols"
