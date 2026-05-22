Feature: Regression-panel runner — assertion engine
  Validates the engine that backs `tradepro-regression-panel`. The
  YAML at the repo root is the source of truth for system-behaviour
  expectations; this feature pins the runner's evaluation of those
  assertions against synthetic rows so a future tweak can't silently
  change PASS / FAIL / SKIP semantics.

  Scenario: bucket match passes
    Given a regression case asserting bucket "BUY"
    And the compare row has bucket "BUY"
    When I evaluate the case
    Then the case status is "pass"

  Scenario: bucket mismatch fails
    Given a regression case asserting bucket "BUY"
    And the compare row has bucket "WAIT"
    When I evaluate the case
    Then the case status is "fail"

  Scenario: bucket HOLD_OR_AVOID accepts WAIT
    Given a regression case asserting bucket "HOLD_OR_AVOID"
    And the compare row has bucket "WAIT"
    When I evaluate the case
    Then the case status is "pass"

  Scenario: missing row marks the case missing
    Given a regression case asserting bucket "BUY"
    And no compare row is available
    When I evaluate the case
    Then the case status is "missing"

  Scenario: coherence check passes when bucket and entry_signal agree
    Given a regression case asserting coherence_check "PASS"
    And the compare row has bucket "BUY" with entry_signal "BUY"
    When I evaluate the case
    Then the case status is "pass"

  Scenario: coherence check fails when bucket and entry_signal disagree
    Given a regression case asserting coherence_check "PASS"
    And the compare row has bucket "BUY" with entry_signal "WAIT"
    When I evaluate the case
    Then the case status is "fail"

  Scenario: unknown assertion key is reported as skip
    Given a regression case asserting completely_unknown_key "true"
    And the compare row has bucket "BUY"
    When I evaluate the case
    Then the case status is "skip"
