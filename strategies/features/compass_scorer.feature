Feature: COMPASS multi-factor alpha scorer

  Continuous Multi-factor Alpha Scoring: 6 weighted factors produce a
  0–100 score per symbol, mapping to BUY/WATCH/HOLD/TRIM signals with
  HIGH/MEDIUM/LOW conviction.

  All scenarios use a synthetic row dict and pass in pre-computed
  sector_rs_result and eps_revision dicts so no network calls are made.
  The macro regime is patched to a fixed mode (GREEN/AMBER/RED) per scenario.

  # ──────────────────────────────────────────────────────────────────
  # Score is always in valid range
  # ──────────────────────────────────────────────────────────────────

  Scenario: Score is always between 0 and 100
    Given any synthetic row for "TEST"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the score is between 0 and 100

  Scenario: Score is a float not None
    Given any synthetic row for "TEST"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the score is a number

  # ──────────────────────────────────────────────────────────────────
  # Signal thresholds
  # ──────────────────────────────────────────────────────────────────

  Scenario: Score >= 72 produces BUY signal
    Given a row engineered to yield COMPASS score of 80
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the signal is "BUY"

  Scenario: Score 55-71 produces WATCH signal
    Given a row engineered to yield COMPASS score of 62
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the signal is "WATCH"

  Scenario: Score 40-54 produces HOLD signal
    Given a row engineered to yield COMPASS score of 47
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the signal is "HOLD"

  Scenario: Score below 40 produces TRIM signal
    Given a row engineered to yield COMPASS score of 25
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the signal is "TRIM"

  # ──────────────────────────────────────────────────────────────────
  # Conviction grades
  # ──────────────────────────────────────────────────────────────────

  Scenario: Score >= 78 → HIGH conviction
    Given a row engineered to yield COMPASS score of 82
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then conviction is "HIGH"

  Scenario: Score 60-77 → MEDIUM conviction
    Given a row engineered to yield COMPASS score of 68
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then conviction is "MEDIUM"

  Scenario: Score below 60 → LOW conviction
    Given a row engineered to yield COMPASS score of 45
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then conviction is "LOW"

  # ──────────────────────────────────────────────────────────────────
  # Macro gate interaction
  # ──────────────────────────────────────────────────────────────────

  Scenario: AMBER regime dampens BUY to WATCH
    Given a row that scores above 72 (BUY territory)
    And macro regime is AMBER (patched)
    When I compute the COMPASS score
    Then the signal is "WATCH"
    And macro_gated is False

  Scenario: RED regime sets macro_gated=True but does not change score
    Given a row that scores above 72 (BUY territory)
    And macro regime is RED (patched)
    When I compute the COMPASS score
    Then macro_gated is True
    And the raw score is still above 72

  Scenario: GREEN regime leaves signal unchanged
    Given a row that scores above 72 (BUY territory)
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the signal is "BUY"
    And macro_gated is False

  # ──────────────────────────────────────────────────────────────────
  # Graceful degradation
  # ──────────────────────────────────────────────────────────────────

  Scenario: None sector_rs_result still produces valid score
    Given any synthetic row for "NOSEC"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score with sector_rs_result=None
    Then the score is between 0 and 100
    And no exception is raised

  Scenario: None eps_revision still produces valid score
    Given any synthetic row for "NOEPS"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score with eps_revision=None
    Then the score is between 0 and 100
    And no exception is raised

  Scenario: Both sector_rs and eps_revision None still valid
    Given any synthetic row for "MINIMAL"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score with sector_rs_result=None and eps_revision=None
    Then the score is between 0 and 100

  # ──────────────────────────────────────────────────────────────────
  # CompassResult structure
  # ──────────────────────────────────────────────────────────────────

  Scenario: to_dict returns all required keys
    Given any synthetic row for "STRUCT"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    And call to_dict on the result
    Then the dict contains key "symbol"
    And the dict contains key "score"
    And the dict contains key "signal"
    And the dict contains key "conviction"
    And the dict contains key "macro_gated"
    And the dict contains key "macro_mode"
    And the dict contains key "factors"

  Scenario: factors list has 7 entries
    Given any synthetic row for "FACTORS"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the factors list has exactly 7 items

  Scenario: each factor has name, score, weight, contribution
    Given any synthetic row for "FSTRUCT"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then each factor dict has keys: name, score, weight, contribution

  Scenario: factor weights sum to 1.0
    Given any synthetic row for "WEIGHTS"
    And macro regime is GREEN (patched)
    When I compute the COMPASS score
    Then the sum of all factor weights equals 1.0
