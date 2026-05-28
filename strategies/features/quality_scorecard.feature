Feature: Quality Scorecard — equity-quality fundamentals view
  Track 2 module ① per TradePro_Roadmap_May2026.docx. Scores six
  fundamentals (ROE / ROA / FCF margin / D/E / profit margin / current
  ratio) on a 0-10 scale and averages them to a 0-5 star rating. Pure
  function — fed a yfinance-shaped info dict, no live network call.

  Compounder mode lives alongside the Trade engine; this scorecard
  answers "is this asset quality enough to compound in?" — NOT "should
  I buy today?". Vocabulary differs from Track 1 deliberately.

  Scenario: blue-chip-class metrics score 5 stars
    Given a fundamentals info dict with
      | field             | value      |
      | returnOnEquity    | 0.42       |
      | returnOnAssets    | 0.18       |
      | profitMargins     | 0.35       |
      | currentRatio      | 1.8        |
      | debtToEquity      | 45         |
      | freeCashflow      | 60000000000 |
      | totalRevenue      | 200000000000 |
    When I compute the quality scorecard for "MSFT"
    Then the scorecard stars is 5
    And the metric "roe" has score 10
    And the metric "profit_margin" has score 10
    And the metric "debt_to_equity" has score 10
    And the missing_metrics list is empty

  Scenario: weak metrics score 1 star
    Given a fundamentals info dict with
      | field             | value      |
      | returnOnEquity    | 0.03       |
      | returnOnAssets    | 0.01       |
      | profitMargins     | 0.02       |
      | currentRatio      | 0.8        |
      | debtToEquity      | 280        |
      | freeCashflow      | 10000000   |
      | totalRevenue      | 1000000000 |
    When I compute the quality scorecard for "WEAK"
    Then the scorecard stars is less than 2
    And the metric "roe" has score 0
    And the metric "debt_to_equity" has score 2
    And the metric "current_ratio" has score 0

  Scenario: mid-quality metrics score 3 stars
    Given a fundamentals info dict with
      | field             | value     |
      | returnOnEquity    | 0.12      |
      | returnOnAssets    | 0.07      |
      | profitMargins     | 0.10      |
      | currentRatio      | 1.6       |
      | debtToEquity      | 80        |
      | freeCashflow      | 5000000000 |
      | totalRevenue      | 50000000000 |
    When I compute the quality scorecard for "MID"
    Then the scorecard stars is 3
    And the metric "roe" has score 5
    And the metric "debt_to_equity" has score 8

  Scenario: missing fields land in missing_metrics, don't drag the score
    Given a fundamentals info dict with
      | field             | value |
      | returnOnEquity    | 0.30  |
      | profitMargins     | 0.25  |
    When I compute the quality scorecard for "PARTIAL"
    Then the scorecard stars is at least 4
    And the missing_metrics list contains "roa"
    And the missing_metrics list contains "debt_to_equity"
    And the missing_metrics list contains "fcf_margin"
    And the missing_metrics list contains "current_ratio"

  Scenario: empty info dict returns a zero scorecard with all metrics missing
    Given an empty fundamentals info dict
    When I compute the quality scorecard for "UNKNOWN"
    Then the scorecard stars is 0
    And the overall_score is 0
    And the missing_metrics list contains "roe"

  Scenario: debt-to-equity normalisation (yfinance gives 60 meaning ratio 0.6)
    Given a fundamentals info dict with
      | field             | value |
      | debtToEquity      | 60    |
    When I compute the quality scorecard for "DENORM"
    Then the metric "debt_to_equity" raw value is approximately 0.6
    And the metric "debt_to_equity" has score 8

  Scenario: NaN values are treated as missing not as zero
    Given a fundamentals info dict with
      | field             | value |
      | returnOnEquity    | nan   |
      | profitMargins     | 0.20  |
    When I compute the quality scorecard for "NAN"
    Then the missing_metrics list contains "roe"
    And the metric "profit_margin" has score 8

  Scenario: to_dict carries star display string for the UI
    Given a fundamentals info dict with
      | field             | value     |
      | returnOnEquity    | 0.20      |
      | returnOnAssets    | 0.10      |
      | profitMargins     | 0.18      |
      | currentRatio      | 2.1       |
      | debtToEquity      | 60        |
      | freeCashflow      | 10000000000 |
      | totalRevenue      | 50000000000 |
    When I compute the quality scorecard for "DICT"
    Then the to_dict payload has stars_display matching "★★★★☆" or stronger
