Feature: LLM gate catalyst overlay (Phase C-3.1)
  Layers the C-3 catalyst flag onto the LLMSignalGate decision.
  HOLD-direction signal + imminent HIGH-severity catalyst dampens
  the scale (operator agency preserved — no veto). BUY-direction
  signal + imminent HIGH-severity catalyst boosts the scale and
  upgrades APPROVED → APPROVED_BOOSTED for the audit row.

  Background:
    Given an LLMSignalGate with the gate disabled

  Scenario: no catalysts means no flag and no scale change
    When evaluate runs with signal 1.0 and no catalysts
    Then the overlay flag is None
    And the overlay scale_factor is 1.0
    And the catalyst_ids list is empty

  Scenario: imminent high catalyst on HOLD signal dampens the scale
    Given a CatalystHint id 42 kind "election" severity "high" days_until 8
    When evaluate runs with signal 0.0 and the catalyst
    Then the overlay flag is "tech_event_divergence"
    And the overlay scale_factor is 0.5
    And the catalyst_ids list contains 42

  Scenario: imminent high catalyst on BUY signal boosts and promotes action
    Given a CatalystHint id 7 kind "central_bank" severity "high" days_until 5
    When evaluate runs with signal 1.0 and the catalyst
    Then the overlay flag is "tech_event_alignment"
    And the overlay action is "APPROVED_BOOSTED"
    And the overlay scale_factor is 1.25
    And the catalyst_ids list contains 7

  Scenario: medium severity surfaces ids but no flag
    Given a CatalystHint id 99 kind "earnings" severity "medium" days_until 5
    When evaluate runs with signal 0.0 and the catalyst
    Then the overlay flag is None
    And the overlay scale_factor is 1.0
    And the catalyst_ids list contains 99

  Scenario: far-future high catalyst does not trip the flag
    Given a CatalystHint id 101 kind "election" severity "high" days_until 30
    When evaluate runs with signal 0.0 and the catalyst
    Then the overlay flag is None
    And the catalyst_ids list contains 101

  Scenario: past catalyst is informational and does not trip the flag
    Given a CatalystHint id 102 kind "earnings" severity "high" days_until -15
    When evaluate runs with signal 0.0 and the catalyst
    Then the overlay flag is None

  Scenario: undated catalyst surfaces ids but no flag
    Given a CatalystHint id 103 kind "regulatory" severity "high" days_until None
    When evaluate runs with signal 0.0 and the catalyst
    Then the overlay flag is None
    And the catalyst_ids list contains 103

  Scenario: mixed list with one imminent high catalyst tips the divergence flag
    Given a CatalystHint id 1 kind "earnings" severity "medium" days_until 10
    And a CatalystHint id 2 kind "election" severity "high" days_until 9
    And a CatalystHint id 3 kind "operator_note" severity "low" days_until 3
    When evaluate runs with signal 0.0 and the catalysts
    Then the overlay flag is "tech_event_divergence"
    And the catalyst_ids list contains 1
    And the catalyst_ids list contains 2
    And the catalyst_ids list contains 3
