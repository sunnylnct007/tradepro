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

  Scenario: Cross-basket momentum rank surfaces in the template factors
    Given a fact bundle for VUKE.L in BUY bucket
    And the symbol's basket-relative momentum rank is 3 of 13 with top quartile
    When I build a rationale for it
    Then a key factor mentions "Momentum rank 3 of 13"
    And a key factor mentions "Top-quartile basket momentum"
    And every number in the rationale appears in the input facts

  Scenario: Cross-basket valuation flag surfaces in the template factors
    Given a fact bundle for VUKE.L in BUY bucket
    And the symbol's basket-relative valuation flag is "cheap"
    When I build a rationale for it
    Then a key factor mentions "Valuation flag: cheap"

  Scenario: Fair valuation does NOT add a factor (only cheap or expensive do)
    Given a fact bundle for VUKE.L in BUY bucket
    And the symbol's basket-relative valuation flag is "fair"
    When I build a rationale for it
    Then no key factor mentions "Valuation flag"

  Scenario: Missing cross-basket data leaves the rationale clean
    Given a fact bundle for QQQ in BUY bucket
    When I build a rationale for it
    Then no key factor mentions "Momentum rank"
    And no key factor mentions "Valuation flag"

  Scenario: Swing composite total surfaces in template factors
    Given a fact bundle for VUKE.L in BUY bucket
    And the symbol's swing composite score is 6 with verdict STRONG_BUY
    When I build a rationale for it
    Then a key factor mentions "Swing composite 6/8"
    And a key factor mentions "STRONG_BUY"
    And every number in the rationale appears in the input facts
