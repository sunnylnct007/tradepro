Feature: Catalyst sink — extractor → registry POST (Phase C-2)
  The C-2 sink bridges the existing news catalyst extractor to the
  C-1 registry endpoint. Confidence → severity coarsening is pinned
  here so the heuristic stays auditable; the POST body shape is
  pinned so a refactor of CatalystUpsertBody on the .NET side trips
  the test before it trips production.

  Scenario Outline: severity heuristic boundaries
    Given a catalyst with confidence <conf>
    When severity_from_confidence runs
    Then the severity is "<severity>"

    Examples:
      | conf | severity |
      | 1.0  | high     |
      | 0.95 | high     |
      | 0.9  | high     |
      | 0.89 | medium   |
      | 0.7  | medium   |
      | 0.6  | medium   |
      | 0.59 | low      |
      | 0.5  | low      |
      | 0.0  | low      |

  Scenario: upsert body carries every required PascalCase field
    Given a catalyst
      | field      | value                          |
      | kind       | election                       |
      | title      | Colombia election in 10 days   |
      | occurs_on  | 2026-06-13                     |
      | confidence | 0.7                            |
      | rationale  | matched 'election'             |
    When the body is built for symbol "EC"
    Then the body field "Symbol" equals "EC"
    And the body field "Kind" equals "election"
    And the body field "OccursOn" equals "2026-06-13"
    And the body field "Title" equals "Colombia election in 10 days"
    And the body field "Source" equals "extractor.news"
    And the body field "Severity" equals "medium"
    And the body field "Status" equals "active"
    And the body payload nested confidence equals 0.7
    And the body payload nested rationale equals "matched 'election'"

  Scenario: empty news list extracts nothing and never POSTs
    Given an empty news list for symbol "AAPL"
    When extract_and_post runs with a recording fake
    Then 0 POSTs are recorded
    And the summary reports catalysts_extracted = 0
    And the summary reports catalysts_posted = 0

  Scenario: failed POSTs are counted but never raise
    Given a news list with 2 dated election headlines for symbol "EC"
    When extract_and_post runs with a recording fake that fails every POST
    Then catalysts_extracted equals catalysts_failed
    And catalysts_posted is 0
    And no exception bubbles up
