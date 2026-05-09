Feature: Gem Hunter — contrarian / mean-reversion scanner
  Phase G. Existing strategies favour uptrends. Gem hunter is the
  inverted lens: quality names beaten down to a real entry. Every
  required check must pass AND ≥1 recovery signal must fire AND no
  disqualifier (hostile sentiment, value-trap profile) trips.

  # ----- Happy path -----
  Scenario: quality stock down 38%, near 52w low, RSI bouncing → IS a gem
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is a gem
    And the passing reasons mention "Sharpe 0.85"
    And the passing reasons mention "-38.0% from 5y peak"
    And the passing reasons mention "18th pctile"
    And the recovery signals mention "bouncing out of oversold"

  # ----- Required-check failures -----
  Scenario: not down enough — only 10% off 5y peak → not a gem
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -10% from 5y peak, 18th pctile, CHEAP, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "not a real correction"

  Scenario: range position 32nd (just above 25th cap) → not a gem
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 32nd pctile, CHEAP, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "not near the floor"

  Scenario: low Sharpe — quality not intact
    Given a gem-profile row: Sharpe 0.3, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "quality threshold"

  Scenario: valuation FAIR — not in cheap quartile
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, FAIR, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "not in the cheap quartile"

  # ----- Disqualifiers -----
  Scenario: hostile sentiment trips the floor
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 38, above SMA200, sentiment -0.45
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "actively wrong"

  Scenario: no recovery signal (RSI 22, below SMA200, negative z) → not a gem
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 22, below SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "no recovery signal"
