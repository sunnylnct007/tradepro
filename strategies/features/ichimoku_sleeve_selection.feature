Feature: Ichimoku equity top-N-by-conviction sleeve selection
  # Root cause of the pre-open "universe_too_large_for_capital" sweeps: the
  # strategy emitted an entry for EVERY signalling name, so a large universe
  # produced more orders than $50k could fund and the excess got bulk-cancelled.
  # Fix: at session start, rank each sleeve's signalling names by conviction
  # (distance of the close above the Ichimoku cloud) and keep only the top
  # `size` — held names occupy their slots first, the rest fill with the
  # strongest new candidates. Deployed capital can never exceed the budget.

  Scenario: only the top-N highest-conviction names per sleeve are selected to enter
    Given three above-cloud names STRONG, MED, WEAK with decreasing conviction
    And a sleeve "core" containing STRONG, MED, WEAK with 2 slots
    When the equity strategy starts the session
    Then the selected entries are exactly STRONG and MED
    And WEAK is dropped as below the conviction cut

  Scenario: a name already held occupies a slot so only the remaining slots fill
    Given three above-cloud names STRONG, MED, WEAK with decreasing conviction
    And a sleeve "core" containing STRONG, MED, WEAK with 2 slots
    And WEAK is already held from the broker seed
    When the equity strategy starts the session
    Then the selected entries are exactly STRONG
