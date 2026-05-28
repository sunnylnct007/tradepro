Feature: market_state — closes_30d field for downstream charting
  The MarketState dataclass carries a `closes_30d` list (the last 30
  split-adjusted closes, oldest → newest). It feeds the email digest's
  BUY-sparkline strip and the PDF per-symbol page. Pin the contract so
  a refactor can't silently empty it or drop the key from to_dict().

  Scenario: closes_30d holds the last 30 closes when series is long
    Given a synthetic price series of 260 daily closes
    When I compute the market state
    Then closes_30d has 30 entries
    And the last value of closes_30d equals the last close
    And closes_30d is serialised in to_dict()

  Scenario: closes_30d gracefully degrades for short series
    Given a synthetic price series of 12 daily closes
    When I compute the market state
    Then closes_30d has 12 entries

  Scenario: NaN entries are filtered out of closes_30d
    Given a synthetic price series of 30 closes with 3 NaNs at the end
    When I compute the market state
    Then closes_30d has 27 entries
    And no entry of closes_30d is NaN
