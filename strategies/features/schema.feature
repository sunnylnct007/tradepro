Feature: Compare payload schema validates strictly
  Bumps to the schema must be additive (new optional fields). Any
  breaking change should fail validation loudly so it can't sneak
  into a deploy.

  Scenario: Minimal payload validates
    Given a minimal compare payload dict
    When I validate it via ComparePayload
    Then validation succeeds
    And the schema_version is the current SCHEMA_VERSION

  Scenario: Payload without rows fails validation
    Given a payload missing the "rows" field with rows replaced by a string
    When I validate it via ComparePayload
    Then validation fails with a list-type error

  Scenario: Bucket demotion thresholds round-trip
    Given a CompareLlmDemotionRule with threshold -0.3 and min_material 2
    When I serialise and re-validate
    Then the values are preserved exactly
