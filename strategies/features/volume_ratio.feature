Feature: market_state — volume conviction ratio (today vs 20-day avg)
  Today's bar volume divided by the trailing 20-day mean (excluding
  today) surfaces whether a price move is happening on conviction
  or thin air. Thresholds mirror the IBD rule-of-thumb (>1.5x heavy,
  <0.8x thin). The bucket vote layer can later demote breakouts on
  light volume; this feature pins the data + trace contract.

  Scenario: heavy volume (today 2x the 20d average) flags as conviction
    Given a 60-bar price series with constant volume 1000000 and today's volume 2000000
    When I compute the market state
    Then volume_ratio_20d is approximately 2.0
    And the trace contains a "Volume vs 20-day average" row with status "pass"
    And the trace detail for "Volume vs 20-day average" mentions "heavy"

  Scenario: thin volume (today 0.5x the 20d average) flags as low conviction
    Given a 60-bar price series with constant volume 1000000 and today's volume 500000
    When I compute the market state
    Then volume_ratio_20d is approximately 0.5
    And the trace contains a "Volume vs 20-day average" row with status "fail"
    And the trace detail for "Volume vs 20-day average" mentions "thin"

  Scenario: normal volume range produces a soft warn (not a fail)
    Given a 60-bar price series with constant volume 1000000 and today's volume 1000000
    When I compute the market state
    Then volume_ratio_20d is approximately 1.0
    And the trace contains a "Volume vs 20-day average" row with status "warn"
    And the trace detail for "Volume vs 20-day average" mentions "normal"

  Scenario: index without a volume column degrades to None (no signal)
    Given a 60-bar price series with no volume column
    When I compute the market state
    Then volume_ratio_20d is None
    And the trace contains a "Volume vs 20-day average" row with status "warn"
    And the trace detail for "Volume vs 20-day average" mentions "—"

  Scenario: < 21 bars is not enough to compute a meaningful ratio
    Given a 15-bar price series with constant volume 1000000 and today's volume 5000000
    When I compute the market state
    Then volume_ratio_20d is None

  Scenario: to_dict() includes volume_ratio_20d so the API payload carries it
    Given a 60-bar price series with constant volume 1000000 and today's volume 1800000
    When I compute the market state
    Then to_dict() carries volume_ratio_20d
