Feature: Dividend Dashboard — Track 2 module ③
  Per TradePro_Roadmap_May2026.docx §Track 2 module 3. Surfaces
  current yield, 5y dividend CAGR, payout ratio, consecutive growth
  years, and projected £ income for a Compounder sleeve holding.

  Verdict vocabulary (distinct from Track 1):
    STRONG          yield ≥ 2% AND CAGR ≥ 7% AND payout ≤ 70%
    STEADY          paying but not best-in-class
    UNDER_PRESSURE  payout > 80% OR negative CAGR OR weak yield+growth
    NONE            no dividend programme

  Scenario: SCHD-class compounder reads STRONG
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 0.034 |
      | payoutRatio     | 0.65  |
    And dividend history
      | year | total |
      | 2020 | 1.60  |
      | 2021 | 1.85  |
      | 2022 | 2.10  |
      | 2023 | 2.36  |
      | 2024 | 2.55  |
      | 2025 | 2.80  |
    When I compute the dividend dashboard for "SCHD"
    Then the dividend verdict is "STRONG"
    And the dividend yield_pct is approximately 3.4
    And the dividend five_year_cagr_pct is greater than 6

  Scenario: high-payout struggling name reads UNDER_PRESSURE
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 0.061 |
      | payoutRatio     | 0.94  |
    And no dividend history
    When I compute the dividend dashboard for "RISKY"
    Then the dividend verdict is "UNDER_PRESSURE"
    And the dividend rationale mentions "payout"

  Scenario: dividend cut shows up as negative CAGR
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 0.025 |
      | payoutRatio     | 0.50  |
    And dividend history
      | year | total |
      | 2020 | 3.00  |
      | 2021 | 2.50  |
      | 2022 | 2.00  |
      | 2023 | 1.50  |
      | 2024 | 1.00  |
      | 2025 | 0.80  |
    When I compute the dividend dashboard for "CUT"
    Then the dividend verdict is "UNDER_PRESSURE"
    And the dividend rationale mentions "declined"

  Scenario: non-payer reads NONE
    Given a dividend info dict with
      | field           | value |
    And no dividend history
    When I compute the dividend dashboard for "GROWTH"
    Then the dividend verdict is "NONE"

  Scenario: modest yield + decent CAGR reads STEADY
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 0.018 |
      | payoutRatio     | 0.35  |
    And dividend history
      | year | total |
      | 2020 | 0.95  |
      | 2021 | 1.00  |
      | 2022 | 1.06  |
      | 2023 | 1.12  |
      | 2024 | 1.18  |
      | 2025 | 1.25  |
    When I compute the dividend dashboard for "STEADY"
    Then the dividend verdict is "STEADY"

  Scenario: projected income computed when position size provided
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 0.034 |
    And no dividend history
    And a position size of 5000 GBP
    When I compute the dividend dashboard for "SCHD"
    Then the projected_annual_income_gbp is approximately 170.0

  Scenario: yfinance percent-form yields are passed through (3.4 not 0.034)
    Given a dividend info dict with
      | field           | value |
      | dividendYield   | 3.4   |
    And no dividend history
    When I compute the dividend dashboard for "PCTFORM"
    Then the dividend yield_pct is approximately 3.4
