Feature: Push payload scrubs NaN / Inf to JSON null
  The comparator emits NaN as a 'missing stat' sentinel (defensive
  cast in _safe_float). Python's JSON serialiser raises on those by
  default and the .NET API rejects them too, so the push wire path
  must replace NaN / Inf with null before serialising. A real run
  surfaced this as 'Out of range float values are not JSON
  compliant: nan' from requests, retrying to exhaustion.

  Scenario: Top-level NaN / Inf become null
    Given a payload with floats nan, +inf, -inf, and 3.14
    When I scrub the payload for JSON
    Then nan and the infs become null
    And finite floats are preserved
    And the result serialises with json.dumps without raising

  Scenario: NaN nested inside dicts and lists is scrubbed
    Given a deeply nested payload with NaN inside a list inside a dict
    When I scrub the payload for JSON
    Then no NaN survives anywhere in the structure
    And the result serialises with json.dumps without raising
