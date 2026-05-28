Feature: Valuation Layer — ATTRACTIVE / FAIR / STRETCHED
  Track 2 module ② per TradePro_Roadmap_May2026.docx. Compounder-mode
  signal vocabulary deliberately differs from Track 1's BUY/WAIT/AVOID:
  this answers "what's the price tag versus the asset?", not "should I
  trade today?".

  Five metrics with absolute thresholds (v1; v2 will add 5y-avg + sector
  median comparisons once Polygon fundamentals timeseries lands):
    trailing_pe          attractive < 12, stretched > 25
    forward_pe           attractive < 12, stretched > 22
    price_to_book        attractive < 1.5, stretched > 3.5
    enterprise_to_ebitda attractive < 10, stretched > 20
    peg_ratio            attractive < 1.0, stretched > 2.0

  Aggregation: ≥ 2 attractive and 0 stretched → ATTRACTIVE; ≥ 2
  stretched and 0 attractive → STRETCHED; mix → FAIR; all missing →
  UNKNOWN. Conservative ties favour FAIR.

  Scenario: deep-value name reads ATTRACTIVE across the board
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 8.5   |
      | forwardPE          | 9.0   |
      | priceToBook        | 1.1   |
      | enterpriseToEbitda | 7.5   |
      | pegRatio           | 0.8   |
    When I compute the valuation layer for "VALUE"
    Then the overall verdict is "ATTRACTIVE"
    And the metric "trailing_pe" verdict is "ATTRACTIVE"

  Scenario: stretched momentum name reads STRETCHED
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 45    |
      | forwardPE          | 38    |
      | priceToBook        | 12    |
      | enterpriseToEbitda | 28    |
      | pegRatio           | 3.5   |
    When I compute the valuation layer for "GROWTH"
    Then the overall verdict is "STRETCHED"

  Scenario: middling metrics read FAIR
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 18    |
      | forwardPE          | 16    |
      | priceToBook        | 2.8   |
      | enterpriseToEbitda | 14    |
      | pegRatio           | 1.5   |
    When I compute the valuation layer for "FAIRVAL"
    Then the overall verdict is "FAIR"

  Scenario: mix of attractive + stretched lands at FAIR (signal unclear)
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 9     |
      | forwardPE          | 8.5   |
      | priceToBook        | 12    |
      | enterpriseToEbitda | 26    |
      | pegRatio           | 0.7   |
    When I compute the valuation layer for "MIXED"
    Then the overall verdict is "FAIR"
    And the valuation rationale mentions "mixed"

  Scenario: empty info dict reads UNKNOWN
    Given an empty valuation info dict
    When I compute the valuation layer for "BLANK"
    Then the overall verdict is "UNKNOWN"
    And the valuation rationale mentions "empty"

  Scenario: negative trailingPE (loss-making) is treated as missing
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | -15   |
      | forwardPE          | 18    |
      | priceToBook        | 4.5   |
    When I compute the valuation layer for "NEGEPS"
    Then the metric "trailing_pe" raw value is missing
    And the valuation missing_metrics list contains "trailing_pe"

  Scenario: NaN values are treated as missing not as zero
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | nan   |
      | forwardPE          | 14    |
    When I compute the valuation layer for "NAN"
    Then the valuation missing_metrics list contains "trailing_pe"
    And the metric "forward_pe" verdict is "FAIR"

  Scenario: ETF basket valuation works the same way
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 17    |
      | priceToBook        | 3.0   |
    When I compute the valuation layer for "SCHD"
    Then the overall verdict is "FAIR"

  Scenario: one ATTRACTIVE alone is not enough — needs at least 2
    Given a valuation info dict with
      | field              | value |
      | trailingPE         | 9     |
      | forwardPE          | 16    |
      | priceToBook        | 2.8   |
    When I compute the valuation layer for "SOLO"
    Then the overall verdict is "FAIR"
