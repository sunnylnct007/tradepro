Feature: GDELT macro/event catalyst source (Phase 17.1)
  The GDELT 2.0 DOC API is the free, key-less macro/event source feeding
  the catalyst overlay. It runs a small set of curated macro queries
  (FOMC / CPI / OPEC / elections / geopolitics) and maps articles to
  dated Catalyst objects on the relevant market-proxy symbol. Every
  failure mode is fail-open — a catalyst source must never break the
  daily sweep. The HTTP layer is mocked in every scenario; no live
  network.

  Scenario: a GDELT article maps to a dated Catalyst on the proxy symbol
    Given a fake GDELT that returns articles
      | title                                  | seendate         | url                | domain      |
      | Fed holds rates steady at June meeting  | 20260607T143000Z | http://ex.com/fed  | reuters.com |
    When GDELT catalysts are fetched
    Then the symbol "SPY" has 1 catalyst
    And the SPY catalyst kind is "macro"
    And the SPY catalyst occurs_on is "2026-06-07"
    And the SPY catalyst confidence is 0.8
    And the SPY catalyst source-tagged body uses source "gdelt"

  Scenario: duplicate articles for one theme collapse to a single catalyst
    Given a fake GDELT that returns articles
      | title                                  | seendate         | url                 | domain      |
      | Fed holds rates steady at June meeting  | 20260607T143000Z | http://ex.com/fed1  | reuters.com |
      | Fed holds rates steady at June meeting  | 20260607T150000Z | http://ex.com/fed2  | bloomberg.com |
    When GDELT catalysts are fetched
    Then the symbol "SPY" has 1 catalyst

  Scenario: an undated article still produces a catalyst with occurs_on None
    Given a fake GDELT that returns articles
      | title                       | seendate | url               | domain   |
      | OPEC weighs deeper cuts     |          | http://ex.com/op  | wsj.com  |
    When GDELT catalysts are fetched
    Then the symbol "XLE" has 1 catalyst
    And the XLE catalyst occurs_on is None

  Scenario: an HTTP error fails open with an empty result and never raises
    Given a fake GDELT that raises ConnectionError
    When GDELT catalysts are fetched
    Then no symbols have catalysts
    And no exception bubbles up from GDELT

  Scenario: a non-2xx response fails open
    Given a fake GDELT that returns status 503
    When GDELT catalysts are fetched
    Then no symbols have catalysts

  Scenario: a non-JSON body fails open
    Given a fake GDELT that returns a non-JSON body
    When GDELT catalysts are fetched
    Then no symbols have catalysts

  Scenario: an article with no title is dropped
    Given a fake GDELT that returns articles
      | title | seendate         | url               | domain   |
      |       | 20260607T143000Z | http://ex.com/x   | x.com    |
    When GDELT catalysts are fetched
    Then no symbols have catalysts
