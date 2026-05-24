Feature: EPS revision tracker

  Weekly snapshots of analyst forward-EPS estimates, stored per symbol.
  get_eps_revision() computes the 90-day delta and direction so COMPASS
  can use earnings-estimate momentum as an alpha factor.

  All scenarios use a temporary directory for snapshot storage — no
  writes to the real ~/.tradepro/eps_snapshots/ path.

  # ──────────────────────────────────────────────────────────────────
  # record_snapshot
  # ──────────────────────────────────────────────────────────────────

  Scenario: record_snapshot persists a valid forwardEps
    Given a temporary snapshot directory
    And a ticker factory returning forwardEps=10.00 for "TESTSYM"
    When I call record_snapshot for "TESTSYM"
    Then the snapshot file for "TESTSYM" exists
    And the file contains 1 entry with forward_eps 10.00

  Scenario: record_snapshot returns None when forwardEps is missing
    Given a temporary snapshot directory
    And a ticker factory returning forwardEps=None for "ETFSYM"
    When I call record_snapshot for "ETFSYM"
    Then the EPS record result is None
    And no snapshot file is created for "ETFSYM"

  Scenario: Same-day duplicate is deduplicated
    Given a temporary snapshot directory
    And a ticker factory returning forwardEps=8.00 for "DEDUP"
    When I call record_snapshot for "DEDUP" twice on the same day
    Then the snapshot file for "DEDUP" contains exactly 1 entry

  Scenario: A second snapshot on a different day appends
    Given a temporary snapshot directory with one snapshot for "GROW" dated "2026-01-05" eps=10.0
    And a ticker factory returning forwardEps=12.0 for "GROW"
    When I call record_snapshot for "GROW"
    Then the snapshot file for "GROW" contains 2 entries

  Scenario: Snapshot file is capped at 104 entries
    Given a temporary snapshot directory with 105 snapshots for "OVERFLOW"
    And a ticker factory returning forwardEps=1.0 for "OVERFLOW"
    When I call record_snapshot for "OVERFLOW"
    Then the snapshot file for "OVERFLOW" has at most 104 entries

  # ──────────────────────────────────────────────────────────────────
  # get_eps_revision
  # ──────────────────────────────────────────────────────────────────

  Scenario: Upward revision over 90 days
    Given a temporary snapshot directory with snapshots for "BULLSYM"
      | date       | forward_eps |
      | 2026-01-15 | 15.10       |
      | 2026-04-15 | 19.88       |
    When I call get_eps_revision for "BULLSYM"
    Then direction is "up"
    And revision_pct is approximately 31.6
    And delta_90d is approximately 4.78
    And current_estimate is 19.88

  Scenario: Downward revision over 90 days
    Given a temporary snapshot directory with snapshots for "BEARSYM"
      | date       | forward_eps |
      | 2026-01-15 | 8.00        |
      | 2026-04-15 | 6.00        |
    When I call get_eps_revision for "BEARSYM"
    Then direction is "down"
    And revision_pct is approximately -25.0

  Scenario: Flat revision (< 1 cent change)
    Given a temporary snapshot directory with snapshots for "FLATSYM"
      | date       | forward_eps |
      | 2026-01-15 | 5.005       |
      | 2026-04-15 | 5.005       |
    When I call get_eps_revision for "FLATSYM"
    Then direction is "flat"

  Scenario: No snapshots returns insufficient_data
    Given a temporary snapshot directory
    When I call get_eps_revision for "NOSYM"
    Then direction is "insufficient_data"
    And snapshots_count is 0

  Scenario: Single snapshot (< 30 days old) returns insufficient_data
    Given a temporary snapshot directory with 1 snapshot for "NEWSYM" dated today
    When I call get_eps_revision for "NEWSYM"
    Then direction is "insufficient_data"

  Scenario: get_eps_revision populates snapshots_count
    Given a temporary snapshot directory with 5 snapshots for "COUNTSYM"
    When I call get_eps_revision for "COUNTSYM"
    Then snapshots_count is 5

  Scenario: as_of reflects the most recent snapshot date
    Given a temporary snapshot directory with snapshots for "DATESYM"
      | date       | forward_eps |
      | 2026-03-01 | 7.0         |
      | 2026-04-15 | 7.5         |
    When I call get_eps_revision for "DATESYM"
    Then as_of is "2026-04-15"
