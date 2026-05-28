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

  # NVDA-style growth name with near-zero dividend used to flag
  # "expensive" under yield-only ranking — wrong signal. P/E lens
  # ranks ascending so lowest P/E in basket is cheapest.
  Scenario: P/E quartile valuation flag — lowest P/E is cheap
    Given the pe basket json {"NVDA": 28.0, "MSFT": 32.0, "AMZN": 45.0, "META": 24.0}
    When I bucket the basket by P/E ratio
    Then "META" has flag "cheap"
    And "AMZN" has flag "expensive"
    And the basis for "META" mentions "P/E"

  Scenario: P/E quartile drops loss-making (negative P/E) symbols
    Given the pe basket json {"A": 20.0, "B": -5.0, "C": 30.0, "D": 40.0}
    When I bucket the basket by P/E ratio
    Then "B" has flag "n/a"

  # Hybrid orchestrator: stocks have P/E coverage, prefers P/E lens.
  Scenario: hybrid valuation picks P/E when basket has P/E coverage
    Given the pe basket json {"NVDA": 28.0, "MSFT": 32.0, "AMZN": 45.0, "META": 24.0}
    And the yield basket json {"NVDA": 0.02, "MSFT": 1.1, "AMZN": 0.0, "META": 0.5}
    When I bucket the basket by valuation
    Then the lens used is "pe"
    And "META" has flag "cheap"

  # ETF basket: no P/E reported, falls back to yield lens (legacy
  # behaviour preserved).
  Scenario: hybrid valuation falls back to yield when P/E missing
    Given the pe basket json {"VUKE.L": null, "VUSA.L": null, "INRG.L": null, "AGGG.L": null}
    And the yield basket json {"VUKE.L": 3.5, "VUSA.L": 1.4, "INRG.L": 0.5, "AGGG.L": 4.2}
    When I bucket the basket by valuation
    Then the lens used is "yield"
    And "AGGG.L" has flag "cheap"
    And "INRG.L" has flag "expensive"

  Scenario: trace rows — top-quartile momentum + cheap valuation = both pass
    Given a top-quartile momentum signal with zscore 1.2
    And a cheap valuation flag
    When I build cross-basket trace rows
    Then there are 2 trace rows
    And the momentum row has status "pass"
    And the valuation row has status "pass"
    And the momentum row detail mentions "rank"

  Scenario: below-median momentum + expensive = both fail
    Given a below-median momentum signal with zscore -0.8
    And an expensive valuation flag
    When I build cross-basket trace rows
    Then the momentum row has status "fail"
    And the valuation row has status "fail"

  Scenario: middle-of-pack momentum is warn, fair valuation is warn
    Given a mid-basket momentum signal with zscore 0.3
    And a fair valuation flag
    When I build cross-basket trace rows
    Then the momentum row has status "warn"
    And the valuation row has status "warn"

  Scenario: missing data omits trace rows entirely
    Given no cross-basket signals
    When I build cross-basket trace rows
    Then there are 0 trace rows
