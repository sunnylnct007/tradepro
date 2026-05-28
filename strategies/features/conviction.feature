Feature: Conviction classification + BUG-001 veto
  Three-tier conviction (HIGH/MEDIUM/LOW/INVALID) per
  IMPROVEMENT_SUGGESTIONS_v1.md §1.3. LOW is the safety net for
  BUG-001 — when trend filters fail, conviction caps at LOW and the
  bucket-cap helper demotes any standing BUY to WAIT. The combination
  is a belt-and-braces guarantee on top of the existing trend gate
  (task #70): even if compute_bucket regresses and lets a BUY through
  with price below the 200d SMA, the conviction veto holds the line
  before the row ships.

  # ─────────── compute_conviction ───────────

  Scenario: trend break by SMA200 forces LOW
    Given a market_state with above_sma_200 false and ichimoku above-cloud
    When I compute conviction with bucket "BUY"
    Then the conviction is "LOW"
    And the conviction reason mentions "below 200d SMA"

  Scenario: trend break by Ichimoku forces LOW
    Given a market_state with above_sma_200 true and ichimoku below-cloud
    When I compute conviction with bucket "BUY"
    Then the conviction is "LOW"
    And the conviction reason mentions "Ichimoku"

  Scenario: sentiment demotion caps at MEDIUM
    Given a market_state with above_sma_200 true and ichimoku above-cloud
    When I compute conviction with bucket "WAIT" and sentiment_demoted true
    Then the conviction is "MEDIUM"
    And the conviction reason mentions "Sentiment"

  Scenario: horizon demotion caps at MEDIUM
    Given a market_state with above_sma_200 true and ichimoku above-cloud
    When I compute conviction with bucket "WAIT" and horizon_demoted true
    Then the conviction is "MEDIUM"
    And the conviction reason mentions "Horizon"

  Scenario: BUY with volume confirmation promotes to HIGH
    Given a market_state with above_sma_200 true and ichimoku above-cloud and volume_ratio_20d 1.50
    When I compute conviction with bucket "BUY"
    Then the conviction is "HIGH"
    And the conviction reason mentions "volume"

  Scenario: BUY without volume confirmation stays at MEDIUM
    Given a market_state with above_sma_200 true and ichimoku above-cloud and volume_ratio_20d 1.00
    When I compute conviction with bucket "BUY"
    Then the conviction is "MEDIUM"
    And the conviction reason mentions "volume confirmation absent"

  Scenario: missing volume data does not promote to HIGH
    Given a market_state with above_sma_200 true and ichimoku above-cloud
    When I compute conviction with bucket "BUY"
    Then the conviction is "MEDIUM"

  Scenario: WAIT with healthy trend stays at MEDIUM
    Given a market_state with above_sma_200 true and ichimoku above-cloud
    When I compute conviction with bucket "WAIT"
    Then the conviction is "MEDIUM"

  # ─────────── cap_bucket_at_low_conviction ───────────

  Scenario: LOW conviction caps a standing BUY at WAIT
    Given a bucket "BUY" with reason "majority long" and conviction "LOW"
    When I cap the bucket at low conviction
    Then the capped bucket is "WAIT"
    And the conviction-demoted flag is True
    And the capped reason mentions "BUG-001"

  Scenario: LOW conviction leaves WAIT alone
    Given a bucket "WAIT" with reason "only 2 of 5 long" and conviction "LOW"
    When I cap the bucket at low conviction
    Then the capped bucket is "WAIT"
    And the conviction-demoted flag is False

  Scenario: MEDIUM conviction leaves a BUY alone
    Given a bucket "BUY" with reason "majority long + volume confirms" and conviction "MEDIUM"
    When I cap the bucket at low conviction
    Then the capped bucket is "BUY"
    And the conviction-demoted flag is False

  Scenario: HIGH conviction leaves a BUY alone
    Given a bucket "BUY" with reason "trend + consensus + volume" and conviction "HIGH"
    When I cap the bucket at low conviction
    Then the capped bucket is "BUY"
    And the conviction-demoted flag is False
