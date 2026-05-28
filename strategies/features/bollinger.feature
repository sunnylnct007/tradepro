Feature: Bollinger Bands — indicator math, decision-trace #7, bounce strategy
  Bollinger Bands(20, 2σ) act as the geometric mean-reversion gate the
  RSI threshold can't capture on its own. Pin the math + the trace +
  the bollinger_bounce strategy so a tweak can't silently weaken the
  oversold-entry filter.

  Scenario: indicator returns five columns and the bands sit symmetrically around middle
    Given a synthetic closing series of 50 bars varying around 100
    When I compute the bollinger indicator
    Then the result has columns middle, upper, lower, bandwidth, percent_b
    And the last upper is greater than the last middle
    And the last lower is less than the last middle

  Scenario: market_state flags AT_LOWER when price closes below the lower band
    Given a synthetic OHLC series sitting at 60 with the prior 40 bars at 100
    When I compute the market state
    Then bollinger_position is "AT_LOWER"
    And the trace contains a "Bollinger Bands (20, 2σ)" row with status "warn"
    And the trace detail for "Bollinger Bands (20, 2σ)" mentions "oversold"

  Scenario: bollinger_bounce fires +1 when price dips below lower and RSI is oversold
    Given a synthetic OHLC series sitting at 60 with the prior 40 bars at 100
    When I generate bollinger_bounce signals
    Then the signal series contains at least one +1

  Scenario: bollinger_bounce stays at 0 when price drifts within the band
    Given a synthetic closing series of 50 bars varying around 100
    When I generate bollinger_bounce signals on the same series as OHLC
    Then the signal series has no +1
