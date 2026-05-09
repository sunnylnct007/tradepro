Feature: Risk rating per recommendation
  Phase R. Every BUY / WAIT / AVOID needs a risk rating so the user
  can size positions correctly. Volatility sets a baseline tier;
  escalators (slow DD recovery, material-negatives, near-highs BUY,
  cross-basket outlier) bump the rating up by one each, capped at
  +2 from baseline. The factor list is the audit trail — every input
  that drove the rating appears verbatim.

  # ----- Vol baseline -----
  Scenario: low-vol ETF (12%) gets LOW with no escalators
    Given a row with vol 12% and no escalators
    When I compute the risk rating
    Then the rating is "LOW"
    And the baseline is "LOW"
    And the escalators count is 0

  Scenario: medium vol (22%) gets MEDIUM with no escalators
    Given a row with vol 22% and no escalators
    When I compute the risk rating
    Then the rating is "MEDIUM"

  Scenario: high vol (32%) gets HIGH
    Given a row with vol 32% and no escalators
    When I compute the risk rating
    Then the rating is "HIGH"

  Scenario: extreme vol (45%) gets EXTREME
    Given a row with vol 45% and no escalators
    When I compute the risk rating
    Then the rating is "EXTREME"

  Scenario: missing vol falls back to MEDIUM (over-warn rather than silent)
    Given a row with no vol data
    When I compute the risk rating
    Then the rating is "MEDIUM"
    And the factors mention "vol unknown"

  # ----- Escalators -----
  Scenario: slow recovery bumps LOW → MEDIUM
    Given a row with vol 14% and 1500-day historical DD recovery
    When I compute the risk rating
    Then the rating is "MEDIUM"
    And the factors mention "slow historical recovery"

  Scenario: BUY at 95th pctile bumps MEDIUM → HIGH
    Given a row with vol 22% and BUY at 95th pctile of 52w range
    When I compute the risk rating
    Then the rating is "HIGH"
    And the factors mention "95th percentile"

  Scenario: 4 material-negatives bumps LOW → MEDIUM
    Given a row with vol 13% and 4 material-negative headlines in 7d
    When I compute the risk rating
    Then the rating is "MEDIUM"
    And the factors mention "material-negative"

  # ----- Cap -----
  Scenario: escalators cap at +2 — LOW with all escalators tops out at HIGH
    Given a row with vol 12%, 1500-day DD recovery, 4 material-negatives, BUY at 95th pctile, z-score +3.2
    When I compute the risk rating
    Then the rating is "HIGH"
    And the escalators count is 2

  Scenario: position cap follows the rating
    Given a "LOW" risk rating
    When I look up the position cap
    Then the cap is 25.0
