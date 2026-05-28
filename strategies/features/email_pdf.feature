Feature: Email-digest PDF builder
  Pin the regression where the PDF showed "0 BUY candidates" while
  the HTML body correctly counted 29 — caused by the PDF's
  _filter_bucket only reading p["rows"] when the API envelope wraps
  rows under p["payload"]["rows"]. Both shapes must work.

  Scenario: builds non-empty bytes when a payload has BUY rows
    Given a payload envelope with 1 BUY row in API shape (payload.rows)
    When I build the digest PDF
    Then the PDF is non-empty

  Scenario: counts the BUY row when payload uses API envelope shape (payload.rows)
    Given a payload envelope with 1 BUY row in API shape (payload.rows)
    When I filter buckets in the PDF
    Then the BUY count is 1

  Scenario: counts the BUY row when payload uses top-level rows shape
    Given a payload envelope with 1 BUY row in top-level shape (rows)
    When I filter buckets in the PDF
    Then the BUY count is 1

  Scenario: empty payloads list returns empty bytes (cleanly)
    Given an empty payloads list
    When I build the digest PDF
    Then the PDF is empty

  Scenario: deduplicates a symbol that appears in multiple universes
    Given two payload envelopes each containing the same NVDA BUY row
    When I filter buckets in the PDF
    Then the BUY count is 1
