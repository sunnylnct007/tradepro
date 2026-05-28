Feature: Manual MF Sleeve — Track 2 module ⑦
  Per TradePro_Roadmap_May2026.docx §Track 2 module 7. UK ISA / Indian
  / offshore mutual funds that have no live API — the user manually
  enters NAV + date + units + fund-currency cost basis. The sleeve
  computes GBP-normalised market value, NAV freshness, regional / asset
  mix, distribution-income projection, and sleeve-vs-target percent.

  NAV freshness vocabulary (per holding):
    FRESH        NAV ≤ stale_threshold_days (default 7)
    STALE        stale_threshold ≤ age < very_stale_threshold (default 30)
    VERY_STALE   age ≥ very_stale_threshold
    UNKNOWN      no parseable NAV date

  Sleeve freshness summary:
    ALL_FRESH    every NAV fresh
    SOME_STALE   < half stale
    MANY_STALE   ≥ half stale (triggers warning)
    EMPTY        no holdings

  Sleeve status: UNDERWEIGHT / ON_TARGET / OVERWEIGHT / UNKNOWN, per
  ±tolerance band around target_sleeve_pct (default 25%).

  # ───────── empty / single-currency basics ─────────

  Scenario: empty holdings list returns a clean zero-shape sleeve
    Given an MF sleeve with no holdings
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve market value is approximately 0
    And the MF sleeve nav_freshness is "EMPTY"
    And the MF sleeve status is "UNKNOWN"
    And the MF sleeve has 0 holdings

  Scenario: single UK fund in GBP — no FX conversion
    Given an MF holding
      | fund_name        | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type | region |
      | Vanguard FTSE UK | 100   | 12.50    | 2026-05-22    | GBP      | 1100             | equity    | UK     |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve market value is approximately 1250
    And the MF sleeve unrealised gain is approximately 150
    And the MF sleeve unrealised gain pct is approximately 13.6
    And the MF holding "Vanguard FTSE UK" nav_status is "FRESH"

  # ───────── multi-currency / FX ─────────

  Scenario: Indian + UK mix — INR fund converts to GBP via FX rate
    Given an MF FX rate INR -> GBP 0.0095
    And an MF holding
      | fund_name           | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type | region |
      | HDFC Top 100 Direct | 500   | 800      | 2026-05-23    | INR      | 350000           | equity    | IN     |
    And an MF holding
      | fund_name        | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type | region |
      | Vanguard FTSE UK | 100   | 12.50    | 2026-05-23    | GBP      | 1100             | equity    | UK     |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF holding "HDFC Top 100 Direct" market_value_gbp is approximately 3800
    And the MF holding "Vanguard FTSE UK" market_value_gbp is approximately 1250
    And the MF sleeve market value is approximately 5050
    And the MF sleeve region_mix_pct "IN" is approximately 75.2
    And the MF sleeve region_mix_pct "UK" is approximately 24.8

  Scenario: missing FX rate adds a warning and excludes that holding from GBP totals
    Given an MF holding
      | fund_name      | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type |
      | Random EUR Fnd | 100   | 50       | 2026-05-23    | EUR      | 4500             | equity    |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve warnings mention "missing FX rate for EUR"
    And the MF sleeve market value is approximately 0

  # ───────── NAV freshness ─────────

  Scenario: NAV 8 days old reads STALE
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Stale Fnd | 100   | 10       | 2026-05-16    | GBP      | 1000             |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF holding "Stale Fnd" nav_status is "STALE"
    And the MF sleeve nav_freshness is "MANY_STALE"
    And the MF sleeve stale_count is 1

  Scenario: NAV 45 days old reads VERY_STALE
    Given an MF holding
      | fund_name      | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Very Stale Fnd | 100   | 10       | 2026-04-09    | GBP      | 1000             |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF holding "Very Stale Fnd" nav_status is "VERY_STALE"
    And the MF sleeve warnings mention "NAVs are stale"

  Scenario: half-fresh half-stale reads SOME_STALE not MANY_STALE
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Fresh A   | 100   | 10       | 2026-05-23    | GBP      | 950              |
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Fresh B   | 100   | 10       | 2026-05-22    | GBP      | 970              |
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Stale C   | 100   | 10       | 2026-05-15    | GBP      | 950              |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve nav_freshness is "SOME_STALE"
    And the MF sleeve stale_count is 1

  Scenario: future-dated NAV flagged as a warning but reads FRESH
    Given an MF holding
      | fund_name   | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Future Fund | 100   | 10       | 2026-06-01    | GBP      | 950              |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF holding "Future Fund" nav_status is "FRESH"
    And the MF sleeve warnings mention "future-dated NAV"

  # ───────── asset / region mix ─────────

  Scenario: equity + debt + hybrid type mix sums per asset class
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type |
      | Eq A      | 100   | 20       | 2026-05-23    | GBP      | 1800             | equity    |
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type |
      | Debt B    | 100   | 10       | 2026-05-23    | GBP      | 950              | debt      |
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | fund_type |
      | Hybrid C  | 100   | 10       | 2026-05-23    | GBP      | 1000             | hybrid    |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve type_mix_pct "equity" is approximately 50.0
    And the MF sleeve type_mix_pct "debt" is approximately 25.0
    And the MF sleeve type_mix_pct "hybrid" is approximately 25.0

  # ───────── yield + SIP projection ─────────

  Scenario: distribution yield drives projected annual income
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | distribution_yield_pct |
      | Income F  | 100   | 100      | 2026-05-23    | GBP      | 9500             | 4.5                    |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve projected annual income is approximately 450

  Scenario: monthly SIP totals across holdings (FX-normalised)
    Given an MF FX rate INR -> GBP 0.0095
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | monthly_sip_local |
      | UK SIP    | 50    | 10       | 2026-05-23    | GBP      | 450              | 100               |
    And an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local | monthly_sip_local |
      | IN SIP    | 200   | 50       | 2026-05-23    | INR      | 8000             | 5000              |
    When I compute the MF sleeve as of "2026-05-24"
    Then the MF sleeve monthly SIP is approximately 147.5

  # ───────── sleeve vs target ─────────

  Scenario: sleeve under target reads UNDERWEIGHT
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Eq A      | 100   | 10       | 2026-05-23    | GBP      | 950              |
    When I compute the MF sleeve as of "2026-05-24" with total portfolio 10000 and target 25
    Then the MF sleeve status is "UNDERWEIGHT"
    And the MF sleeve sleeve_pct_of_portfolio is approximately 10.0

  Scenario: sleeve on target reads ON_TARGET (within ±2.5 band)
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Eq A      | 250   | 10       | 2026-05-23    | GBP      | 2400             |
    When I compute the MF sleeve as of "2026-05-24" with total portfolio 10000 and target 25
    Then the MF sleeve status is "ON_TARGET"

  Scenario: sleeve over target reads OVERWEIGHT
    Given an MF holding
      | fund_name | units | last_nav | last_nav_date | currency | cost_basis_local |
      | Eq A      | 400   | 10       | 2026-05-23    | GBP      | 3800             |
    When I compute the MF sleeve as of "2026-05-24" with total portfolio 10000 and target 25
    Then the MF sleeve status is "OVERWEIGHT"
