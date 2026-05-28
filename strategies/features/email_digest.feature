Feature: Daily email digest builder
  The pure builder takes compare payloads and returns subject + text
  + html — no I/O, no SMTP, behave-friendly. The transport (SMTP) is
  in the CLI, not here. Pin the structural contract so a future
  refactor doesn't silently change the column order or omit a row.

  Scenario: BUY rows surface in the BUY block with key stats
    Given a compare payload with one BUY symbol "VUKE.L" in universe "etf_uk_core"
    When I build the email digest
    Then the subject mentions "1 BUY"
    And the text body contains "VUKE.L"
    And the text body contains "etf_uk_core"
    And the html body has a BUY heading
    And the html body has a row with the symbol

  Scenario: AVOID and WAIT rows are bucketed separately
    Given a compare payload with 1 AVOID "OLDCO" and 2 WAIT "X.L,Y.L" symbols
    When I build the email digest
    Then the subject mentions "1 AVOID"
    And the subject mentions "2 WAIT"
    And the text body has BUY block marked "(none today)"

  Scenario: drawdown recovery time is rendered when present
    Given a compare payload with one BUY symbol that fully recovered from drawdown
    When I build the email digest
    Then the text body shows "(recovered" with day count

  Scenario: a position still in drawdown is rendered as still recovering
    Given a compare payload with one BUY symbol still in drawdown
    When I build the email digest
    Then the text body shows "(still recovering)"

  Scenario: empty payloads yield a digest that says so cleanly
    Given an empty list of compare payloads
    When I build the email digest
    Then the subject mentions "0 BUY"
    And the text body contains "(none today)"

  Scenario: stale data (market closed) surfaces a banner in both bodies
    Given a compare payload whose latest bar is 2 days old
    When I build the email digest
    Then the text body contains "markets closed today"
    And the html body contains "markets closed today"

  Scenario: cross-basket momentum rank surfaces in the BUY block
    Given a compare payload with one BUY symbol that ranks top in basket on momentum
    When I build the email digest
    Then the text body contains "Momentum rank 1/5"
    And the text body contains "top quartile"

  Scenario: valuation flag surfaces in the BUY block
    Given a compare payload with one BUY symbol flagged cheap
    When I build the email digest
    Then the text body contains "Valuation cheap"

  Scenario: a row with neither cross-basket signal omits the line cleanly
    Given a compare payload with one BUY symbol that has no cross-basket annotations
    When I build the email digest
    Then the text body contains "VUKE.L"
    And the text body does not contain "Momentum rank"
    And the text body does not contain "Valuation cheap"
