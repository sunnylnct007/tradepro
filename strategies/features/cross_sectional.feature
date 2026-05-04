Feature: Cross-sectional momentum ranks within the basket
  Existing strategies all live in Family 1 (price vs its own moving
  average). They tend to agree, so the bucket vote alone can't tell
  apart "strong vs basket" from "everyone is strong". Family-3
  cross-sectional rank fixes that — each row gets its rank + zscore
  vs peers on 12-month return.

  Scenario: highest momentum gets rank 1, lowest gets rank N
    Given the basket json {"A": 25.0, "B": 15.0, "C": 5.0, "D": -3.0}
    When I rank the basket by momentum
    Then "A" has rank 1
    And "D" has rank 4
    And "A" rank_pct is 1.0

  Scenario: zscore reflects distance from basket mean
    Given the basket json {"A": 30.0, "B": 10.0, "C": 10.0, "D": 10.0}
    When I rank the basket by momentum
    Then "A" zscore is positive
    And "B" zscore is negative

  Scenario: symbols with missing data return rank None
    Given the basket json {"A": 12.0, "B": null, "C": 8.0}
    When I rank the basket by momentum
    Then "B" has rank None
    And "B" zscore is None
    And "A" peer_count is 1

  Scenario: top-quartile flag for the strongest names
    Given a basket of 8 symbols with monotonically decreasing returns
    When I rank the basket by momentum
    Then exactly 2 symbols are flagged top quartile

  Scenario: empty / single-symbol basket degrades gracefully
    Given a basket of one symbol
    When I rank the basket by momentum
    Then the single symbol has zscore 0.0

  Scenario: yield-quartile valuation flag — top quartile is cheap
    Given the yield basket json {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0}
    When I bucket the basket by yield quartile
    Then "A" has flag "cheap"
    And "E" has flag "expensive"
    And the basis for "A" mentions the basket median

  Scenario: missing yield gets n/a flag
    Given the yield basket json {"A": 4.0, "B": null, "C": 2.0}
    When I bucket the basket by yield quartile
    Then "B" has flag "n/a"

  Scenario: empty yield basket falls back gracefully
    Given the yield basket json {"A": null, "B": null}
    When I bucket the basket by yield quartile
    Then "A" has flag "n/a"
    And "B" has flag "n/a"
