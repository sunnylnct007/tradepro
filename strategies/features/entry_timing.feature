Feature: Entry Timing Assist — dip-accumulation alert
  Track 2 module ⑤ per TradePro_Roadmap_May2026.docx. Explicitly NOT a
  buy signal — a dip-accumulation alert combining quality + valuation
  + drawdown.

  Verdict logic (all three must agree for ACCUMULATE):
    quality   ≥ 4 stars
    valuation ATTRACTIVE
    drawdown  ≥ 10% from 52w high

  Verdict vocabulary:
    ACCUMULATE     all 3 signals pass
    WATCH          2 of 3 pass
    NEUTRAL        ≤ 1 of 3 passes
    INSUFFICIENT   any input missing (don't pretend)

  Scenario: 5-star compounder + ATTRACTIVE + 15% off high → ACCUMULATE
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    When I compute entry timing for "MSFT"
    Then the entry verdict is "ACCUMULATE"
    And signals_passing is 3
    And the entry rationale mentions "accumulation zone"

  Scenario: STRETCHED valuation kills accumulation despite quality + drawdown
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "STRETCHED"
    And drawdown of 15.0 percent from 52w high
    When I compute entry timing for "QUALITY"
    Then the entry verdict is "WATCH"
    And signals_passing is 2
    And the entry rationale mentions "valuation"

  Scenario: low quality is a value trap signal — never ACCUMULATE
    Given a quality scorecard with stars 2
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 30.0 percent from 52w high
    When I compute entry timing for "TRAP"
    Then the entry verdict is "WATCH"
    And signals_passing is 2
    And the entry rationale mentions "quality"

  Scenario: small drawdown isn't a meaningful dip
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 4.0 percent from 52w high
    When I compute entry timing for "NEAR_HIGH"
    Then the entry verdict is "WATCH"
    And signals_passing is 2
    And the entry rationale mentions "drawdown"

  Scenario: drawdown at the threshold (10%) still counts
    Given a quality scorecard with stars 4
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 10.0 percent from 52w high
    When I compute entry timing for "EDGE"
    Then the entry verdict is "ACCUMULATE"

  Scenario: only one signal passing → NEUTRAL
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "STRETCHED"
    And drawdown of 3.0 percent from 52w high
    When I compute entry timing for "NEAR_PEAK_QUALITY"
    Then the entry verdict is "NEUTRAL"
    And signals_passing is 1

  Scenario: zero signals passing → NEUTRAL
    Given a quality scorecard with stars 2
    And a valuation layer with verdict "STRETCHED"
    And drawdown of 3.0 percent from 52w high
    When I compute entry timing for "OVERPRICED"
    Then the entry verdict is "NEUTRAL"
    And signals_passing is 0

  Scenario: missing quality scorecard reads INSUFFICIENT
    Given no quality scorecard
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    When I compute entry timing for "NOQUAL"
    Then the entry verdict is "INSUFFICIENT"
    And the entry rationale mentions "quality"

  Scenario: UNKNOWN valuation reads INSUFFICIENT
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "UNKNOWN"
    And drawdown of 15.0 percent from 52w high
    When I compute entry timing for "NOVAL"
    Then the entry verdict is "INSUFFICIENT"

  Scenario: market_state-derived drawdown (production path)
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And a market_state with pct_off_52w_high_pct 18.5
    When I compute entry timing for "MSFT"
    Then the entry verdict is "ACCUMULATE"
    And the entry drawdown is approximately 18.5

  # ──────────────────────────────────────────────────────────────────
  # Lane A's multi-year A-F grade as the preferred quality signal
  # ──────────────────────────────────────────────────────────────────

  Scenario: grade A passes the quality gate even when ★ would not
    Given a quality scorecard with stars 2
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    And a long-term grade "A"
    When I compute entry timing for "MULTIYEAR_WINS"
    Then the entry verdict is "ACCUMULATE"
    And the entry quality_source is "grade"
    And the entry rationale mentions "grade A"

  Scenario: grade B also passes the quality gate
    Given a quality scorecard with stars 3
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 12.0 percent from 52w high
    And a long-term grade "B"
    When I compute entry timing for "STRONG_TRENDS"
    Then the entry verdict is "ACCUMULATE"
    And the entry quality_source is "grade"

  Scenario: grade C fails the quality gate even when ★ would pass
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    And a long-term grade "C"
    When I compute entry timing for "SNAPSHOT_WAS_LUCKY"
    Then the entry verdict is "WATCH"
    And the entry quality_source is "grade"
    And the entry rationale mentions "quality"

  Scenario: grade F fails the quality gate
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 20.0 percent from 52w high
    And a long-term grade "F"
    When I compute entry timing for "DETERIORATING"
    Then the entry verdict is "WATCH"
    And the entry quality_source is "grade"

  Scenario: no grade falls back to ★ stars (5★ + ATTRACTIVE + dip → ACCUMULATE)
    Given a quality scorecard with stars 5
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    When I compute entry timing for "FALLBACK"
    Then the entry verdict is "ACCUMULATE"
    And the entry quality_source is "stars"

  Scenario: grade lowercase is normalised
    Given a quality scorecard with stars 2
    And a valuation layer with verdict "ATTRACTIVE"
    And drawdown of 15.0 percent from 52w high
    And a long-term grade "a"
    When I compute entry timing for "NORMALISE"
    Then the entry verdict is "ACCUMULATE"
    And the entry quality_source is "grade"
