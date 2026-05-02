Feature: Plain-English rationale (no hallucination)
  As a careful investor, when I open an ETF's verdict I want the
  prose summary to be either (a) LLM-generated and verified against
  the structured facts, or (b) a deterministic template built
  mechanically from those same facts. Never an unverified number.

  Background:
    Given the LLM provider is the no-op (so tests don't call out)

  Scenario: Template is used when LLM is unavailable
    Given a fact bundle for QQQ in BUY bucket
    When I build a rationale for it
    Then the rationale source is a template variant
    And the rationale is marked verified
    And every number in the rationale appears in the input facts

  Scenario: Verifier rejects fabricated numbers
    Given a rationale that mentions an unsupported "999%" figure
    And a fact bundle containing no such number
    When I run the local verifier
    Then the rationale is rejected as unverified

  Scenario: Template summary cites the bucket reason
    Given a fact bundle for AVOID with reason "below 200-day SMA"
    When I build a rationale for it
    Then the rationale summary mentions the bucket name AVOID
