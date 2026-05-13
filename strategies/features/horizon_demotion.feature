Feature: Horizon + range veto on BUY bucket
  The bucket vote can promote HOLD + majority-strategy-long to BUY,
  which conflates "already in position" with "good time to add". When
  the swing horizon explicitly says AVOID, or when range_pct is at
  the literal top of the 52w range, that BUY should drop to WAIT —
  otherwise we'd flag QUAL / USMV as BUY at the 100th percentile of
  their range, which is the worst possible entry.

  Scenario: swing horizon AVOID downgrades BUY to WAIT
    Given a starting BUY bucket with reason "majority long"
    And horizon_classification swing signal is "AVOID" with score 0
    When I apply the horizon and range demotion
    Then the resulting bucket is "WAIT"
    And the horizon demoted flag is True
    And the horizon demotion reason mentions "swing horizon AVOID"

  Scenario: range_pct at 100th percentile downgrades BUY to WAIT
    Given a starting BUY bucket with reason "majority long"
    And horizon_classification swing signal is "WATCH" with score 4
    And range_pct is 100
    When I apply the horizon and range demotion
    Then the resulting bucket is "WAIT"
    And the horizon demoted flag is True
    And the horizon demotion reason mentions "100th percentile"

  Scenario: range_pct at 60th percentile and horizon WATCH leaves BUY alone
    Given a starting BUY bucket with reason "majority long"
    And horizon_classification swing signal is "WATCH" with score 5
    And range_pct is 60
    When I apply the horizon and range demotion
    Then the resulting bucket is "BUY"
    And the horizon demoted flag is False

  Scenario: WAIT bucket is never promoted by the demotion rule
    Given a starting WAIT bucket with reason "only 2 of 5 strategies long"
    And horizon_classification swing signal is "BUY" with score 7
    And range_pct is 30
    When I apply the horizon and range demotion
    Then the resulting bucket is "WAIT"
    And the horizon demoted flag is False

  Scenario: breakout BUY at the high is preserved when swing horizon also says BUY
    # MU yesterday: new 52w high + Deutsche Bank PT raise + swing horizon
    # found a real catalyst. Range veto should NOT fire — the bucket
    # stays BUY because the event-driven layer agrees this is the entry.
    Given a starting BUY bucket with reason "majority long + breakout"
    And horizon_classification swing signal is "BUY" with score 7
    And range_pct is 100
    When I apply the horizon and range demotion
    Then the resulting bucket is "BUY"
    And the horizon demoted flag is False
