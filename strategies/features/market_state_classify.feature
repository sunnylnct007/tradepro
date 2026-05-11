Feature: market_state._classify — entry signal rule chain
  Pin every branch of the BUY / WAIT / AVOID / HOLD classifier so a
  refactor can't silently change the semantics. This is the function
  feeding every per-symbol verdict in the comparator — the load-
  bearing rule chain.

  # ----- AVOID -----
  Scenario: confirmed downtrend (below SMA200 + weak 12m) → AVOID
    Given a synthetic price series in confirmed downtrend
    When I compute the market state
    Then the entry signal is "AVOID"
    And the entry reason mentions "below 200-day SMA"

  # ----- AVOID (active crash, fires BEFORE bounce-zone BUY) -----
  Scenario: active 10d cascade below SMA200 → AVOID (falling knife)
    Given a synthetic price series in active 10d crash below SMA200
    When I compute the market state
    Then the entry signal is "AVOID"
    And the entry reason mentions "active cascade"
    And the entry reason mentions "do not catch the falling knife"

  # ----- WAIT (overbought-at-highs) -----
  Scenario: at 52w highs with RSI overbought → WAIT
    Given a synthetic price series at 52w highs with overbought RSI
    When I compute the market state
    Then the entry signal is "WAIT"
    And the entry reason mentions "overbought"

  # ----- WAIT (range-guard near highs, BUY downgrade) -----
  Scenario: above SMA200 but at 76th pctile of 52w → WAIT (range-guard)
    Given a synthetic VUKE-shaped price series ending at the 70th+ percentile of its 52w range
    When I compute the market state
    Then the entry signal is "WAIT"
    And the entry reason mentions "percentile of 52w range"

  # ----- BUY (mean-reversion bounce zone) -----
  Scenario: meaningful 52w drawdown with RSI bouncing → BUY (bounce zone)
    Given a synthetic price series 12% off 52w high with RSI 42 recovering
    When I compute the market state
    Then the entry signal is "BUY"
    And the entry reason mentions "off 52w high"
    And the entry reason mentions "bounce zone"

  # ----- BUY (clean uptrend) -----
  # The clean-uptrend BUY rule (above SMA + healthy RSI + not extended)
  # requires pct_off_52w_high in (1, 8) AND range_position_pct < 70.
  # That band is geometrically empty for any realistic high/low ratio:
  # last within 8% of the 52w high implies range_position_pct ≥ ~85%
  # whenever low ≤ 0.7×high. The range-position guard now demotes the
  # whole band to WAIT, so this branch is effectively dead code post-
  # range-guard. Behaviour is covered by the WAIT-near-highs scenario
  # above.

  # ----- HOLD (default) -----
  Scenario: ambiguous setup (no fresh edge) → HOLD
    Given a synthetic price series with no fresh entry edge
    When I compute the market state
    Then the entry signal is "HOLD"
    And the entry reason mentions "no fresh entry edge"

  # ----- WAIT (mid-drawdown not yet stabilised) -----
  Scenario: 12% drawdown from 5y peak (mid zone) → WAIT
    Given a synthetic price series in 12% mid-drawdown
    When I compute the market state
    Then the entry signal is "WAIT"
    And the entry reason mentions "drawdown"
