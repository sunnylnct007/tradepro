Feature: Horizon classification engine — three independent verdicts per symbol
  TRADEPRO-SPEC-001 §6.1. The same instrument at the same price can
  simultaneously be a poor swing entry, a good long-term hold, and an
  excellent passive vehicle. The engine separates those verdicts so
  the user (and the LLM) can reason about each horizon explicitly.

  # ----- VUKE.L (FTSE 100 ETF, near 52w highs, no catalyst) -----
  Scenario: VUKE.L near 52w highs scores swing AVOID
    Given VUKE.L horizon inputs from 8 May 2026
    When I classify horizons
    Then the swing signal is "AVOID"
    And the swing score is 2
    And the swing reasons mention "Near 52w highs"
    And the passive signal is "BUY"

  Scenario: VUKE.L range_pct is preserved on the output
    Given VUKE.L horizon inputs from 8 May 2026
    When I classify horizons
    Then the range_pct is 72.6

  # ----- GOOGL at 52w highs, RSI 80 -----
  Scenario: GOOGL at 95th pctile of range is capped at WATCH on swing
    Given GOOGL horizon inputs at 95th pctile of range
    When I classify horizons
    Then the swing signal is "AVOID"
    And the swing reasons mention "capped at WATCH"

  # ----- NVDA stock — passive returns N/A -----
  Scenario: Individual stocks return N/A on passive horizon
    Given NVDA horizon inputs as a single stock
    When I classify horizons
    Then the passive signal is "N/A"
    And the passive reasons mention "Single-stock"

  # ----- Range modifier: bonus near lows -----
  Scenario: Symbol near 52w lows gets the +1 bonus on swing
    Given a low-pctile symbol with RSI 35 and 12% off the high
    When I classify horizons
    Then the swing reasons mention "Near annual lows"
    And the swing score is at least 6

  # ----- Stocks vs ETFs: long-term works for both -----
  Scenario: Long-term BUY requires quality fundamentals + analyst conviction
    Given a high-quality stock with Sharpe 1.0 and 30% analyst upside
    When I classify horizons
    Then the long_term signal is "BUY"
