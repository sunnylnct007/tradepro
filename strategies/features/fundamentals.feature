Feature: Fundamentals fetch + sanitisation
  Pin the data-quality regressions caught in the 2026-05-09 review:
  yields > 25% must null out (the SIZE 146% / QUAL 91% bug); the
  fraction-vs-percent threshold sits at 0.3 not 1.5; n_holdings
  ignores head-capped tables (the factor-ETF "n_holdings = 6" bug);
  debt/equity + free cash flow are extracted for the gem hunter.

  # ----- _yield_pct fraction-vs-percent heuristic -----
  Scenario: tiny fraction (0.0146) is treated as a fraction → 1.46%
    When I sanitise yield value 0.0146
    Then the result is 1.46

  Scenario: already-percent (1.46) stays as 1.46% (post-fix)
    When I sanitise yield value 1.46
    Then the result is 1.46

  Scenario: implausible 91% nulls out (corruption guard)
    When I sanitise yield value 91
    Then the result is None

  Scenario: implausible 146% nulls out (the SIZE bug)
    When I sanitise yield value 146
    Then the result is None

  Scenario: 25.5% sits just above the cap and nulls out
    When I sanitise yield value 25.5
    Then the result is None

  Scenario: 24.5% sits just below the cap and is preserved
    When I sanitise yield value 24.5
    Then the result is 24.5

  Scenario: None passes through as None
    When I sanitise yield value None
    Then the result is None

  # ----- _frac_to_pct (returns + ratios — no upper cap) -----
  Scenario: large positive return (45%) is preserved
    When I convert fraction 0.45 with _frac_to_pct
    Then the result is 45.0

  Scenario: huge return (150% as fraction) is preserved (no cap)
    When I convert fraction 1.5 with _frac_to_pct
    Then the result is 1.5

  Scenario: deep negative return (-50% as fraction) is preserved
    When I convert fraction -0.50 with _frac_to_pct
    Then the result is -50.0

  # ----- n_holdings head-cap detection -----
  Scenario: 6 rows (looks like a top-10 head cap) returns None
    When I check funds_data holdings count of 6
    Then the holdings count result is None

  Scenario: 10 rows (the most common head cap) returns None
    When I check funds_data holdings count of 10
    Then the holdings count result is None

  Scenario: 12 rows (still suspicious as a cap) returns None
    When I check funds_data holdings count of 12
    Then the holdings count result is None

  Scenario: 50 rows (clearly real basket size) is preserved
    When I check funds_data holdings count of 50
    Then the holdings count result is 50

  Scenario: 700 rows (large factor ETF) is preserved
    When I check funds_data holdings count of 700
    Then the holdings count result is 700
