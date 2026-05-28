Feature: Core Sleeve Allocation View — Track 2 module ④
  Per TradePro_Roadmap_May2026.docx §Track 2 module 4. The ring-fenced
  ~25% sleeve tracker — aggregates Compounder positions (separate
  from swing/intraday) into one allocation view: market value, cost
  basis, unrealised gain, weighted yield, projected £ income,
  monthly DCA inflow, and sleeve-vs-target status.

  Status thresholds (default tolerance ±2.5% around target 25%):
    UNDERWEIGHT  actual < target − tolerance
    ON_TARGET    target − tolerance ≤ actual ≤ target + tolerance
    OVERWEIGHT   actual > target + tolerance
    UNKNOWN      no total_portfolio_value supplied

  Scenario: two-holding sleeve aggregates correctly
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 100      | 7000           | 78.5              | 3.4       | 200                 |
      | VTI    | 30       | 6000           | 220.0             | 1.3       | 150                 |
    When I compute the allocation view with portfolio 40000
    Then the sleeve_market_value_gbp is approximately 14450
    And the sleeve_cost_basis_gbp is approximately 13000
    And the sleeve_unrealised_gain_gbp is approximately 1450
    And the planned_monthly_inflow_gbp is approximately 350
    And the weighted_yield_pct is approximately 2.5
    And the sleeve has 2 position breakdowns

  Scenario: underweight sleeve flagged for top-up
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 50       | 3500           | 78.5              | 3.4       | 0                   |
    When I compute the allocation view with portfolio 40000
    Then the sleeve status is "UNDERWEIGHT"
    And the sleeve_pct_of_portfolio is less than 22.5

  Scenario: overweight sleeve flagged for trim
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 200      | 14000          | 78.5              | 3.4       | 0                   |
    When I compute the allocation view with portfolio 40000
    Then the sleeve status is "OVERWEIGHT"
    And the sleeve_pct_of_portfolio is greater than 27.5

  Scenario: on-target sleeve at exactly 25%
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 100      | 7000           | 100               | 3.4       | 0                   |
    When I compute the allocation view with portfolio 40000
    Then the sleeve status is "ON_TARGET"

  Scenario: no total portfolio value supplied → status UNKNOWN
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 100      | 7000           | 78.5              | 3.4       | 0                   |
    When I compute the allocation view without a portfolio value
    Then the sleeve status is "UNKNOWN"
    And the sleeve_pct_of_portfolio is null

  Scenario: position breakdowns carry weight + projected income
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 100      | 7000           | 80                | 3.4       | 0                   |
      | VTI    | 50       | 9000           | 200               | 1.3       | 0                   |
    When I compute the allocation view with portfolio 40000
    Then the breakdown for "SCHD" has weight approximately 44
    And the breakdown for "VTI" has weight approximately 56
    And the breakdown for "SCHD" has projected_annual_income_gbp approximately 272
    And the breakdown for "VTI" has projected_annual_income_gbp approximately 130

  Scenario: empty sleeve renders cleanly
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
    When I compute the allocation view with portfolio 40000
    Then the sleeve_market_value_gbp is approximately 0
    And the sleeve has 0 position breakdowns

  Scenario: missing yield on one position degrades weighted_yield gracefully
    Given core sleeve positions
      | symbol | quantity | cost_basis_gbp | current_price_gbp | yield_pct | planned_monthly_gbp |
      | SCHD   | 100      | 7000           | 80                | 3.4       | 0                   |
      | TSLA   | 10       | 3000           | 350               |           | 0                   |
    When I compute the allocation view with portfolio 40000
    Then the weighted_yield_pct is approximately 3.4
    And the breakdown for "TSLA" has projected_annual_income_gbp null
