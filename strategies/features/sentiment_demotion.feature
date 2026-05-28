Feature: Two-tier sentiment demotion
  News flow can override the bucket vote in two distinct severities:
  Tier 2 demotes BUY → WAIT (negative backdrop, sit out); Tier 1
  demotes any → AVOID (genuinely hostile flow). Differentiates
  AMZN-class names (mean ≈ -0.475 with 4 material-negs) from
  routine WAITs caused by overbought RSI.

  # ----- Tier 2 (standard demotion, BUY → WAIT) -----
  Scenario: BUY with mean -0.32 and 2 material-negs → WAIT
    Given a BUY with sentiment mean -0.32 and 2 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "WAIT"
    And the demoted flag is set
    And the reason mentions "≤ threshold -0.3"

  Scenario: BUY with mean -0.30 (at threshold) and 2 material-negs → WAIT
    Given a BUY with sentiment mean -0.30 and 2 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "WAIT"

  Scenario: BUY with mean -0.32 but only 1 material-neg → BUY (no demote)
    Given a BUY with sentiment mean -0.32 and 1 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "BUY"
    And the demoted flag is not set

  Scenario: BUY with mean -0.20 → BUY (above threshold)
    Given a BUY with sentiment mean -0.20 and 5 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "BUY"
    And the demoted flag is not set

  # ----- Tier 1 (strong demotion, ANY → AVOID) -----
  Scenario: BUY with mean -0.475 and 4 material-negs → AVOID (the AMZN case)
    Given a BUY with sentiment mean -0.475 and 4 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "AVOID"
    And the demoted flag is set
    And the reason mentions "AVOID"
    And the reason mentions "materially worse"

  Scenario: WAIT with mean -0.50 and 3 material-negs → AVOID (any → AVOID)
    Given a WAIT with sentiment mean -0.50 and 3 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "AVOID"
    And the demoted flag is set

  Scenario: BUY with mean -0.46 but only 2 material-negs → WAIT (Tier 1 needs ≥3)
    Given a BUY with sentiment mean -0.46 and 2 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "WAIT"
    And the demoted flag is set

  # ----- AVOID stays AVOID -----
  Scenario: AVOID with hostile sentiment stays AVOID (no change)
    Given a AVOID with sentiment mean -0.50 and 4 material-negative headlines
    When I apply sentiment demotion
    Then the bucket becomes "AVOID"
    And the demoted flag is not set

  # ----- Missing data -----
  Scenario: BUY with no sentiment data is preserved
    Given a BUY with no sentiment data
    When I apply sentiment demotion
    Then the bucket becomes "BUY"
    And the demoted flag is not set
