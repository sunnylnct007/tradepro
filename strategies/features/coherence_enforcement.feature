Feature: Coherence enforcement on the shipped row
  BUG-002 fix per IMPROVEMENT_SUGGESTIONS_v1.md §1.3 + §4. Every row
  that ships to the UI / MCP / Compare API must have
  `market_state.entry_signal` equal to the final `bucket`, with the
  raw price-action signal preserved as `raw_entry_signal` and a
  top-level `coherence` block exposing the relationship. The
  regression-panel's `coherence_check` resolver compares the two
  fields directly — this feature pins the helper that guarantees
  they agree on output.

  Scenario: agreeing inputs leave the row alone except for the coherence block
    Given a compare row with bucket "BUY" and raw entry_signal "BUY"
    When I enforce coherence on the row
    Then the row's market_state.entry_signal is "BUY"
    And the row has no market_state.raw_entry_signal
    And the row's coherence.consistent flag is true
    And the row's coherence.supersede_reason is null

  Scenario: sentiment demotion is the named supersede reason
    Given a compare row with bucket "WAIT" and raw entry_signal "BUY"
    When I enforce coherence on the row with sentiment_demoted true
    Then the row's market_state.entry_signal is "WAIT"
    And the row's market_state.raw_entry_signal is "BUY"
    And the row's coherence.today_bucket is "WAIT"
    And the row's coherence.entry_signal is "WAIT"
    And the row's coherence.consistent flag is true
    And the row's coherence.supersede_reason is "sentiment_demotion"

  Scenario: horizon demotion is the named supersede reason
    Given a compare row with bucket "WAIT" and raw entry_signal "BUY"
    When I enforce coherence on the row with horizon_demoted true
    Then the row's market_state.entry_signal is "WAIT"
    And the row's market_state.raw_entry_signal is "BUY"
    And the row's coherence.supersede_reason is "horizon_demotion"

  Scenario: neither demotion fired — supersede_reason falls through
    Given a compare row with bucket "WAIT" and raw entry_signal "BUY"
    When I enforce coherence on the row
    Then the row's market_state.entry_signal is "WAIT"
    And the row's coherence.supersede_reason is "consensus_or_factor_fit"

  Scenario: the regression panel's coherence_check passes on every enforced row
    Given a compare row with bucket "WAIT" and raw entry_signal "BUY"
    When I enforce coherence on the row
    Then the regression panel coherence_check resolver returns "pass" for expected "PASS"
