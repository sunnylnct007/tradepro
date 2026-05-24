Feature: ETF X-Ray — holdings overlap + DRIP projection
  Track 2 module ⑥ per TradePro_Roadmap_May2026.docx. Surfaces what's
  inside an ETF wrapper and detects "you're holding two ETFs that are
  largely the same thing".

  The motivating example: a user holds VTI + QQQ + SCHD thinking
  they're diversifying. ETF X-Ray scores the pairwise overlap and
  recommends consolidation when overlap is ≥ 50%.

  Overlap is computed as weight-intersection over top-N holdings:
  for each symbol present in both ETFs, the contribution is
  min(weight_a, weight_b). Total overlap = sum of contributions.

  # ─────────── per-ETF summary ───────────

  Scenario: ETF summary normalises holdings to {symbol, name, weight_pct}
    Given an ETF "SCHD" with holdings
      | symbol | name      | weight_pct |
      | LMT    | Lockheed  | 4.5        |
      | TXN    | TI        | 4.2        |
      | PEP    | Pepsi     | 4.1        |
    And expense_ratio_pct 0.06
    And current_yield_pct 3.4
    When I compute the ETF xray
    Then the xray holding_count is 3
    And the xray expense_ratio_pct is approximately 0.06

  Scenario: fraction-weighted holdings (0.05) are normalised to percent (5.0)
    Given an ETF "VTI" with holdings
      | symbol | name      | weight_pct |
      | AAPL   | Apple     | 0.06       |
      | MSFT   | Microsoft | 0.055      |
    When I compute the ETF xray
    Then the holding "AAPL" weight is approximately 6.0
    And the holding "MSFT" weight is approximately 5.5

  Scenario: holdings without a symbol or weight are dropped
    Given an ETF "MESSY" with holdings
      | symbol | name    | weight_pct |
      | AAPL   | Apple   | 6.0        |
      |        | NoName  | 3.0        |
      | XYZ    | NoWeight |           |
    When I compute the ETF xray
    Then the xray holding_count is 1

  # ─────────── overlap detection ───────────

  Scenario: high-overlap pair flagged for consolidation
    Given ETF "VTI" with holdings
      | symbol | name      | weight_pct |
      | AAPL   | Apple     | 12.0       |
      | MSFT   | Microsoft | 11.0       |
      | NVDA   | Nvidia    | 10.0       |
      | GOOGL  | Alphabet  | 9.0        |
      | AMZN   | Amazon    | 8.0        |
    And ETF "QQQ" with holdings
      | symbol | name      | weight_pct |
      | AAPL   | Apple     | 13.0       |
      | MSFT   | Microsoft | 12.0       |
      | NVDA   | Nvidia    | 11.0       |
      | GOOGL  | Alphabet  | 10.0       |
      | AMZN   | Amazon    | 9.0        |
    When I compute overlap between "VTI" and "QQQ"
    Then the overlap_pct is greater than 49
    And the shared_count is 5
    And the overlap rationale mentions "consolidating"

  Scenario: minimal overlap reads cleanly
    Given ETF "SCHD" with holdings
      | symbol | name      | weight_pct |
      | LMT    | Lockheed  | 4.5        |
      | TXN    | TI        | 4.2        |
      | PEP    | Pepsi     | 4.1        |
    And ETF "ARKK" with holdings
      | symbol | name      | weight_pct |
      | TSLA   | Tesla     | 10.0       |
      | COIN   | Coinbase  | 8.0        |
      | ROKU   | Roku      | 6.0        |
    When I compute overlap between "SCHD" and "ARKK"
    Then the overlap_pct is approximately 0
    And the shared_count is 0
    And the overlap rationale mentions "no shared"

  Scenario: weight-intersection takes the min of the two weights
    Given ETF "A" with holdings
      | symbol | name | weight_pct |
      | AAPL   | A    | 10.0       |
    And ETF "B" with holdings
      | symbol | name | weight_pct |
      | AAPL   | A    | 2.0        |
    When I compute overlap between "A" and "B"
    Then the overlap_pct is approximately 2.0

  Scenario: contributions are sorted by overlap_weight_pct desc
    Given ETF "X" with holdings
      | symbol | name | weight_pct |
      | AAPL   | A    | 8.0        |
      | MSFT   | M    | 6.0        |
      | NVDA   | N    | 4.0        |
    And ETF "Y" with holdings
      | symbol | name | weight_pct |
      | NVDA   | N    | 9.0        |
      | MSFT   | M    | 5.0        |
      | AAPL   | A    | 3.0        |
    When I compute overlap between "X" and "Y"
    Then the first contribution symbol is "MSFT"

  # ─────────── DRIP projection ───────────

  Scenario: DRIP floor case (no price change)
    When I project DRIP from 10000 GBP at 4.0% yield for 10 years with 0.0 percent price change
    Then the projected end_value_gbp is approximately 14802

  Scenario: DRIP with modest price appreciation
    When I project DRIP from 10000 GBP at 4.0% yield for 10 years with 5.0 percent price change
    Then the projected end_value_gbp is greater than 23000

  Scenario: DRIP with zero yield matches price-only path
    When I project DRIP from 10000 GBP at 0.0% yield for 10 years with 7.0 percent price change
    Then the projected end_value_gbp is approximately 19672
    And the dividends_reinvested_gbp is approximately 0
