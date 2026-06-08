Feature: SEC EDGAR filing catalyst source (free, key-less event-driven)
  The SEC EDGAR submissions API is the authoritative, free, key-less source
  for company-specific FILING catalysts: 8-K material events and 10-Q / 10-K
  earnings. Tickers resolve to CIKs via SEC's company_tickers.json map (cached
  in-process, padded to 10 digits); recent filings come from the submissions
  API. High-signal forms within the lookback window map to dated Catalyst
  objects on the filing symbol. SEC requires a descriptive User-Agent or it
  403s, and asks callers to stay under ~10 req/sec. Every failure mode is
  fail-open — a catalyst source must never break the daily sweep. The HTTP
  layer is mocked in every scenario; no live network.

  Background:
    Given the SEC CIK cache is reset
    And a fake SEC ticker map
      | ticker | cik    |
      | AAPL   | 320193 |
      | MSFT   | 789019 |

  Scenario: ticker resolves to a 10-digit zero-padded CIK
    When the CIK for "AAPL" is resolved
    Then the resolved CIK is "0000320193"

  Scenario: an unknown ticker resolves to no CIK and is skipped
    When SEC catalysts are fetched for "SPY" with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 8-K  | 2026-06-05 | 8.01  | 0000320193-26-000001 | a.htm           |
    Then the symbol "SPY" is skipped
    And the symbol "SPY" has 0 catalysts

  Scenario: a 10-Q filing maps to a high-confidence earnings Catalyst
    When SEC catalysts are fetched for "AAPL" with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 10-Q | 2026-06-05 |       | 0000320193-26-000010 | aapl10q.htm     |
    Then the symbol "AAPL" has 1 catalyst
    And the AAPL catalyst kind is "earnings"
    And the AAPL catalyst occurs_on is "2026-06-05"
    And the AAPL catalyst confidence is 0.95
    And the AAPL catalyst link contains "320193"
    And the AAPL catalyst source-tagged body uses source "sec_edgar"

  Scenario: a generic 8-K maps to a medium event Catalyst with an item title
    When SEC catalysts are fetched for "AAPL" with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 8-K  | 2026-06-06 | 8.01  | 0000320193-26-000011 | aapl8k.htm      |
    Then the symbol "AAPL" has 1 catalyst
    And the AAPL catalyst kind is "event"
    And the AAPL catalyst confidence is 0.7
    And the AAPL catalyst title contains "Item 8.01 Other Events"

  Scenario: an 8-K Item 2.02 (results) is promoted to a high earnings Catalyst
    When SEC catalysts are fetched for "AAPL" with filings
      | form | filingDate | items     | accessionNumber      | primaryDocument |
      | 8-K  | 2026-06-06 | 2.02,9.01 | 0000320193-26-000012 | aapl8k.htm      |
    Then the symbol "AAPL" has 1 catalyst
    And the AAPL catalyst kind is "earnings"
    And the AAPL catalyst confidence is 0.95
    And the AAPL catalyst title contains "Item 2.02 Results of Operations"

  Scenario: filings outside the lookback window are dropped
    When SEC catalysts are fetched for "AAPL" within 30 days with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 8-K  | 2026-06-06 | 8.01  | 0000320193-26-000013 | recent.htm      |
      | 8-K  | 2026-01-01 | 8.01  | 0000320193-26-000014 | old.htm         |
    Then the symbol "AAPL" has 1 catalyst
    And the AAPL catalyst occurs_on is "2026-06-06"

  Scenario: Form 4 insider filings are off by default
    When SEC catalysts are fetched for "AAPL" with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 4    | 2026-06-06 |       | 0000320193-26-000015 | form4.xml       |
    Then the symbol "AAPL" has 0 catalysts

  Scenario: duplicate same-day filings collapse to one catalyst
    When SEC catalysts are fetched for "AAPL" with filings
      | form | filingDate | items | accessionNumber      | primaryDocument |
      | 10-K | 2026-06-04 |       | 0000320193-26-000016 | a.htm           |
      | 10-K | 2026-06-04 |       | 0000320193-26-000017 | b.htm           |
    Then the symbol "AAPL" has 1 catalyst

  Scenario: a 403 on the submissions fetch fails open
    When SEC catalysts are fetched for "AAPL" but submissions returns status 403
    Then the symbol "AAPL" has 0 catalysts
    And no exception bubbles up from SEC

  Scenario: a ConnectionError fails open
    When SEC catalysts are fetched for "AAPL" but the network raises ConnectionError
    Then the symbol "AAPL" has 0 catalysts
    And no exception bubbles up from SEC

  Scenario: a non-JSON submissions body fails open
    When SEC catalysts are fetched for "AAPL" but submissions returns a non-JSON body
    Then the symbol "AAPL" has 0 catalysts
    And no exception bubbles up from SEC
