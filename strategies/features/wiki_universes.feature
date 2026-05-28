Feature: Wikipedia symbol-universe scraper
  The trader's universe lists today come from manually opening
  Wikipedia's "List of S&P 500 companies" / "FTSE 100" pages and
  hand-copying tickers. ``tradepro_strategies.universes.wikipedia``
  does that scrape automatically, runs the per-universe ticker
  normaliser (BRK.B → BRK-B for Yahoo, 7203 → 7203.T for Tokyo),
  and isolates errors so one broken page does not kill the daily
  refresh batch.

  All scenarios drive ``parse_universe_html`` / ``fetch_all_universes``
  with synthetic HTML — no scenario in this suite ever hits Wikipedia.

  Scenario: parses an S&P 500 style HTML table and normalises tickers
    Given an inline HTML page with these S&P 500 rows
      | symbol | company        | sector                 |
      | AAPL   | Apple Inc.     | Information Technology |
      | MSFT   | Microsoft      | Information Technology |
      | BRK.B  | Berkshire B    | Financials             |
      | GOOG   | Alphabet C     | Communication Services |
    When I parse the page as the "sp500" universe
    Then the parsed symbols include exactly
      | ticker | sector                 |
      | AAPL   | Information Technology |
      | MSFT   | Information Technology |
      | BRK-B  | Financials             |
      | GOOG   | Communication Services |

  Scenario: FTSE-100 normaliser adds the .L Yahoo suffix
    Given an inline HTML page with these FTSE 100 rows
      | ticker | company         | ftse industry classification benchmark sector |
      | AZN    | AstraZeneca     | Pharmaceuticals                                |
      | RDSA   | Shell A         | Oil & Gas                                      |
      | RDS.A  | Shell A class   | Oil & Gas                                      |
    When I parse the page as the "ftse100" universe
    Then the parsed symbols include exactly
      | ticker   | sector          |
      | AZN.L    | Pharmaceuticals |
      | RDSA.L   | Oil & Gas       |
      | RDS-A.L  | Oil & Gas       |

  Scenario: batch fetch isolates a failing universe
    Given inline HTML for "sp500" with 12 rows
    And inline HTML for "ftse100" that is malformed
    When I fetch all universes (only "sp500" and "ftse100")
    Then the batch result contains "sp500" with at least 12 symbols
    And the batch result records an error for "ftse100"
    And the batch result _errors dict has 1 entry

  Scenario: universe below the floor is treated as a parse failure
    Given inline HTML for "sp500" with 3 rows
    When I fetch all universes (only "sp500")
    Then the batch result records an error for "sp500"
    And the error message for "sp500" contains "below floor"
