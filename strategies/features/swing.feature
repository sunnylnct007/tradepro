Feature: Phase-X swing-trade composite scorer (0-8)
  Combines the four signal families into one number so a user
  doesn't need to read RSI + Sharpe + earnings + valuation
  separately. Verdict mapping: ≥6 STRONG_BUY, 4-5 BUY, 2-3 HOLD,
  0-1 AVOID. Pin the per-layer scoring + the threshold mapping.

  Scenario: all four families positive → STRONG_BUY at 8/8
    Given a row with Sharpe 0.95 and max-DD recovery 200d
    And the row has valuation flag cheap
    And the row has a STRONG beat-and-retreat earnings signal
    And the row has 4 of 5 strategies long with RSI 45 above SMA200
    When I score the row's swing setup
    Then the swing total is 8
    And the swing verdict is "STRONG_BUY"

  Scenario: solid quality + cheap + healthy price, no earnings → BUY at 5
    Given a row with Sharpe 0.8 and max-DD recovery 300d
    And the row has valuation flag cheap
    And the row has no recent earnings event
    And the row has 4 of 5 strategies long with RSI 50 above SMA200
    When I score the row's swing setup
    Then the swing total is 6
    And the swing verdict is "STRONG_BUY"

  Scenario: weak Sharpe + expensive + below SMA → AVOID
    Given a row with Sharpe 0.2 and still recovering from drawdown
    And the row has valuation flag expensive
    And the row has no recent earnings event
    And the row has 1 of 5 strategies long with RSI 28 below SMA200
    When I score the row's swing setup
    Then the swing total is 0
    And the swing verdict is "AVOID"

  Scenario: middling row (Sharpe ok but Recovery slow, fair valuation, no edge) → HOLD
    Given a row with Sharpe 0.4 and max-DD recovery 500d
    And the row has valuation flag fair
    And the row has no recent earnings event
    And the row has 2 of 5 strategies long with RSI 50 above SMA200
    When I score the row's swing setup
    Then the swing verdict is "HOLD"

  Scenario: ETF (NOT_APPLICABLE earnings) doesn't get penalised vs missed-beat
    Given a row with Sharpe 0.85 and max-DD recovery 300d
    And the row has valuation flag fair
    And the row has earnings verdict "NOT_APPLICABLE"
    And the row has 4 of 5 strategies long with RSI 50 above SMA200
    When I score the row's swing setup
    Then the event layer score is 0
    And the event reason mentions "no recent earnings"
    And the swing verdict is "BUY"
