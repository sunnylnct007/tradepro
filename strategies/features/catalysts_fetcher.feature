Feature: CatalystFetcher — strategy → registry HTTP bridge (Phase C-3.2)
  Strategies inject a CatalystFetcher and call fetch(symbol) before
  every gate.evaluate. The fetcher hits /api/catalysts/, projects
  the response into CatalystHint records, and caches per-symbol
  for a short TTL so the registry is not hammered on the hot path.
  All failure modes return [] — the gate's overlay is purely additive.

  Scenario: a 200 response is projected into CatalystHint records
    Given a fetcher with a fake HTTP that returns 200 with catalysts
      | id | kind          | severity | occurs_on_offset |
      | 11 | election      | high     | 8                |
      | 22 | earnings      | medium   | 14               |
      | 33 | operator_note | low      |                  |
    When fetch runs for symbol "EC"
    Then 3 hints are returned
    And the hint with id 11 has severity "high"
    And the hint with id 11 has days_until 8
    And the hint with id 33 has days_until None

  Scenario: a non-2xx response returns an empty list
    Given a fetcher with a fake HTTP that returns 500
    When fetch runs for symbol "EC"
    Then 0 hints are returned

  Scenario: an HTTP exception returns an empty list and never raises
    Given a fetcher with a fake HTTP that raises ConnectionError
    When fetch runs for symbol "EC"
    Then 0 hints are returned

  Scenario: TTL cache skips the second fetch within the window
    Given a fetcher with a fake HTTP that returns 200 with 0 catalysts
    When fetch runs for symbol "EC"
    And fetch runs for symbol "EC" again
    Then the fake HTTP was called 1 time

  Scenario: invalidate clears the cache for one symbol
    Given a fetcher with a fake HTTP that returns 200 with 0 catalysts
    When fetch runs for symbol "EC"
    And invalidate runs for symbol "EC"
    And fetch runs for symbol "EC" again
    Then the fake HTTP was called 2 times

  Scenario: blank symbols return empty without hitting the network
    Given a fetcher with a fake HTTP that returns 200 with 0 catalysts
    When fetch runs for symbol ""
    Then 0 hints are returned
    And the fake HTTP was called 0 times
