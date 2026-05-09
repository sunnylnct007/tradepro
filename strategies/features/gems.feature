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
    And the failed filters mention "below etf floor"

  Scenario: valuation FAIR — not in cheap quartile
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, FAIR, RSI 38, above SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "not in the cheap quartile"

  # ----- Disqualifiers -----
  Scenario: hostile sentiment trips the v2 tighter floor
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 38, above SMA200, sentiment -0.45
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "sentiment mean"

  Scenario: no recovery signal (RSI 22, below SMA200, negative z) → not a gem
    Given a gem-profile row: Sharpe 0.85, recovers in 13mo, -38% from 5y peak, 18th pctile, CHEAP, RSI 22, below SMA200, sentiment -0.05
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "required recovery signals"

  # ----- V2: stock fundamentals quality floor -----
  Scenario: stock with debt/equity 2.5 fails leverage filter
    Given a stock gem-profile row with debt/equity 2.5 and FCF +5B
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "over-leveraged"

  Scenario: stock with negative free cash flow fails the FCF floor
    Given a stock gem-profile row with debt/equity 0.8 and FCF -200M
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "not generating cash"

  # ----- V2: tail-risk sentiment guards -----
  Scenario: 1 very-negative headline trips tail-risk filter (zero allowed)
    Given a stock gem-profile row with 1 very-negative headline
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "very-negative"

  Scenario: 2 material-negative headlines trips the ≤1 cap
    Given a stock gem-profile row with 2 material-negative headlines
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "material-negative"

  # ----- V2: forced HIGH risk + position cap -----
  Scenario: gem qualifies — forced ≥HIGH risk + 5% position cap
    Given a stock gem-profile row with vol 18% (would be MEDIUM)
    When I evaluate it as a gem
    Then it is a gem
    And the forced risk is "HIGH"
    And the position cap is 5.0
    And the passing reasons mention "rated MEDIUM by volatility"

  # ----- V2: stocks need ≥2 recovery signals -----
  Scenario: stock with only 1 recovery signal (RSI bouncing only) → fails
    Given a stock gem-profile row with only RSI 38 bouncing (below SMA200, z negative)
    When I evaluate it as a gem
    Then it is NOT a gem
    And the failed filters mention "1 of 2 required recovery signals"

  # ----- Exit framework -----
  Scenario: RSI 70 + above SMA + recovered to -8% → RECLASSIFIED
    Given a recovered gem position with RSI 70, above SMA200, drawdown -8%
    When I evaluate the gem exit
    Then the exit action is "RECLASSIFIED"

  Scenario: sentiment fell to -0.45 → THESIS_BROKEN
    Given a gem position with sentiment -0.45
    When I evaluate the gem exit
    Then the exit action is "THESIS_BROKEN"

  Scenario: still oversold + sentiment fine → HOLD
    Given a gem position with RSI 38, sentiment -0.05, debt/equity 0.8
    When I evaluate the gem exit
    Then the exit action is "HOLD"

  # ----- Sector concentration banner -----
  Scenario: 5 of 7 gems in same sector → banner fires
    Given 7 gem rows where 5 are in the energy sector
    When I check sector concentration
    Then the banner mentions "energy"
    And the banner mentions "consider sector ETF"

  Scenario: gems spread across sectors → no banner
    Given 7 gem rows with one in each of 7 different sectors
    When I check sector concentration
    Then no sector banner fires
