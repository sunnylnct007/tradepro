Feature: Symbol Analysis Card — unified technical + fundamental view
  TradePro's defining surface (user, 2026-05-24): "the whole idea of
  tradepro is to have a platform to show technical and fundamental
  analysis". One card per symbol, fusing the compare-row technical
  block with Quality + Valuation + Dividend + Entry-Timing fundamentals
  and the other dev's long-term grade.

  The card always returns a primary_horizon_recommendation token
  answering "is this short / medium / long-term?" — priority order:
    AVOID             grade F  OR  technical AVOID + valuation STRETCHED
    LONG_TERM_HOLD    grade A/B + valuation not STRETCHED + dividend STRONG/STEADY
    MEDIUM_TERM_ADD   entry_timing ACCUMULATE
    SHORT_TERM_TRADE  technical BUY + conviction HIGH/MEDIUM + rr_gate passed
    WATCH             technical WAIT OR quality < ★★★
    INSUFFICIENT      fallthrough

  All scenarios pass info dicts + fixture compare_row / long_term_result
  so there is no network. skip_long_term=True is implied when no fixture
  long_term_result is supplied.

  # ──────────────────────────────────────────────────────────────────
  # AVOID
  # ──────────────────────────────────────────────────────────────────

  Scenario: long-term grade F overrides any short-term technical bid
    Given a symbol-card fundamentals info dict with
      | field         | value |
      | trailingPE    | 9     |
      | forwardPE     | 9     |
    And a symbol-card long_term_result with grade "F"
    And a symbol-card compare_row with bucket "BUY" and conviction "HIGH"
    When I build the symbol analysis card for "BROKEN"
    Then the card primary_horizon_recommendation is "AVOID"
    And the card rationale mentions "grade F"

  Scenario: technical AVOID + valuation STRETCHED → AVOID
    Given a symbol-card fundamentals info dict with
      | field              | value |
      | trailingPE         | 45    |
      | forwardPE          | 38    |
      | priceToBook        | 12    |
      | enterpriseToEbitda | 28    |
      | pegRatio           | 3.5   |
    And a symbol-card compare_row with bucket "AVOID" and conviction "LOW"
    When I build the symbol analysis card for "OVERHEATED"
    Then the card primary_horizon_recommendation is "AVOID"
    And the card rationale mentions "both lenses"

  # ──────────────────────────────────────────────────────────────────
  # LONG_TERM_HOLD
  # ──────────────────────────────────────────────────────────────────

  Scenario: grade A + valuation ATTRACTIVE + dividend STRONG → LONG_TERM_HOLD
    Given a symbol-card fundamentals info dict with
      | field              | value      |
      | trailingPE         | 8.5        |
      | forwardPE          | 9.0        |
      | priceToBook        | 1.1        |
      | enterpriseToEbitda | 7.5        |
      | pegRatio           | 0.8        |
      | dividendYield      | 0.034      |
      | payoutRatio        | 0.65       |
    And symbol-card dividend history
      | year | total |
      | 2020 | 1.60  |
      | 2021 | 1.85  |
      | 2022 | 2.10  |
      | 2023 | 2.36  |
      | 2024 | 2.55  |
      | 2025 | 2.80  |
    And a symbol-card long_term_result with grade "A"
    When I build the symbol analysis card for "SCHD"
    Then the card primary_horizon_recommendation is "LONG_TERM_HOLD"
    And the card rationale mentions "compounder"

  # ──────────────────────────────────────────────────────────────────
  # MEDIUM_TERM_ADD — entry_timing ACCUMULATE
  # ──────────────────────────────────────────────────────────────────

  Scenario: 5★ quality + ATTRACTIVE valuation + 15% drawdown → MEDIUM_TERM_ADD
    Given a symbol-card fundamentals info dict with
      | field              | value       |
      | returnOnEquity     | 0.42        |
      | returnOnAssets     | 0.18        |
      | profitMargins      | 0.35        |
      | currentRatio       | 1.8         |
      | debtToEquity       | 45          |
      | freeCashflow       | 60000000000 |
      | totalRevenue       | 200000000000|
      | trailingPE         | 8.5         |
      | forwardPE          | 9.0         |
      | priceToBook        | 1.1         |
      | enterpriseToEbitda | 7.5         |
      | pegRatio           | 0.8         |
    And a symbol-card drawdown of 15.0 percent
    When I build the symbol analysis card for "DIPPING_QUALITY"
    Then the card primary_horizon_recommendation is "MEDIUM_TERM_ADD"
    And the card rationale mentions "ACCUMULATE"

  # ──────────────────────────────────────────────────────────────────
  # SHORT_TERM_TRADE
  # ──────────────────────────────────────────────────────────────────

  Scenario: technical BUY + HIGH conviction + RR gate passed → SHORT_TERM_TRADE
    Given a symbol-card fundamentals info dict with
      | field      | value |
      | trailingPE | 18    |
    And a symbol-card compare_row with bucket "BUY" and conviction "HIGH"
    And the symbol-card technical rr_gate passed True
    When I build the symbol analysis card for "MOMENTUM"
    Then the card primary_horizon_recommendation is "SHORT_TERM_TRADE"
    And the card rationale mentions "RR gate passed"

  Scenario: SHORT_TERM_TRADE blocked when RR gate fails
    Given a symbol-card fundamentals info dict with
      | field      | value |
      | trailingPE | 18    |
    And a symbol-card compare_row with bucket "BUY" and conviction "HIGH"
    And the symbol-card technical rr_gate passed False
    When I build the symbol analysis card for "BADRR"
    Then the card primary_horizon_recommendation is not "SHORT_TERM_TRADE"

  # ──────────────────────────────────────────────────────────────────
  # WATCH
  # ──────────────────────────────────────────────────────────────────

  Scenario: technical WAIT bucket → WATCH
    Given a symbol-card fundamentals info dict with
      | field      | value |
      | trailingPE | 18    |
    And a symbol-card compare_row with bucket "WAIT" and conviction "LOW"
    When I build the symbol analysis card for "SIDEWAYS"
    Then the card primary_horizon_recommendation is "WATCH"
    And the card rationale mentions "Mixed signals"

  Scenario: low-star fundamentals with no clear technical → WATCH
    Given a symbol-card fundamentals info dict with
      | field            | value     |
      | returnOnEquity   | 0.03      |
      | returnOnAssets   | 0.01      |
      | profitMargins    | 0.02      |
      | currentRatio     | 0.8       |
      | debtToEquity     | 280       |
      | freeCashflow     | 10000000  |
      | totalRevenue     | 1000000000|
    When I build the symbol analysis card for "LOWSTAR"
    Then the card primary_horizon_recommendation is "WATCH"

  # ──────────────────────────────────────────────────────────────────
  # INSUFFICIENT
  # ──────────────────────────────────────────────────────────────────

  Scenario: mid-quality with no technical row → INSUFFICIENT (nothing else fits)
    Given a symbol-card fundamentals info dict with
      | field            | value      |
      | returnOnEquity   | 0.12       |
      | returnOnAssets   | 0.07       |
      | profitMargins    | 0.10       |
      | currentRatio     | 1.6        |
      | debtToEquity     | 80         |
      | freeCashflow     | 5000000000 |
      | totalRevenue     | 50000000000|
      | trailingPE       | 18         |
    When I build the symbol analysis card for "MID"
    Then the card primary_horizon_recommendation is "INSUFFICIENT"

  # ──────────────────────────────────────────────────────────────────
  # Shape contract — both lenses always present in payload
  # ──────────────────────────────────────────────────────────────────

  Scenario: card always carries the fundamental block; technical only when row supplied
    Given a symbol-card fundamentals info dict with
      | field      | value |
      | trailingPE | 18    |
    When I build the symbol analysis card for "SHAPE"
    Then the card payload has a fundamental block
    And the card payload technical block is null

  Scenario: card carries technical block when compare_row is supplied
    Given a symbol-card fundamentals info dict with
      | field      | value |
      | trailingPE | 18    |
    And a symbol-card compare_row with bucket "BUY" and conviction "HIGH"
    When I build the symbol analysis card for "SHAPE2"
    Then the card payload has a fundamental block
    And the card payload technical bucket is "BUY"
