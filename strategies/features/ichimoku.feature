Feature: Ichimoku Cloud — indicator math + entry/exit + cloud-position trace
  The cloud strategy is the first one in TradePro that emits forward
  price targets and a stop level. Pin the math (Tenkan/Kijun midrange,
  Senkou shift) and the strategy entry/exit gating so future tweaks
  can't silently re-interpret a BUY as no-action.

  Scenario: indicator produces the eight Ichimoku lines on a clean uptrend
    Given a synthetic OHLC series of 100 bars trending up at 1% per bar
    When I compute the ichimoku indicator with defaults
    Then the result has columns tenkan, kijun, senkou_a, senkou_b, chikou, cloud_high, cloud_low, cloud_thickness
    And the last cloud_high is greater than or equal to the last cloud_low

  Scenario: strategy fires +1 on a clean cloud-break breakout
    Given a synthetic OHLC series that breaks above its forward cloud on the last bar
    When I generate ichimoku_cloud signals
    Then the latest signal is 1

  # ABOVE-cloud setups have already broken the cloud; senkou_b
  # therefore sits BELOW the current price as a trail-stop support
  # band, NOT as a take-profit. Using it as a "target" gave a
  # nonsense negative R/R for MTUM 2026-05-20 ("→ 249.87 · stop
  # 287.21 · R/R -4.0×" on a WAIT verdict). The fix nulls
  # price_target / rr_ratio when reward would be ≤ 0; stop_level
  # still surfaces because the kijun line IS the invalidation level
  # regardless of whether we'd enter today.
  Scenario: ichimoku_targets nulls price_target when price has broken above the cloud (MTUM regression)
    Given a synthetic OHLC series sitting above its forward cloud
    When I compute the ichimoku_targets envelope
    Then stop_level is a positive number
    And cloud_position equals "ABOVE"
    And price_target is None
    And rr_ratio is None

  Scenario: market_state cloud-position trace fires when price is above the cloud
    Given a synthetic OHLC series sitting above its forward cloud
    When I compute the market state
    Then the trace contains a "Ichimoku cloud position" row with status "pass"

  Scenario: market_state cloud-position trace warns when below the minimum 78 bars
    Given a synthetic OHLC series of 40 bars
    When I compute the market state
    Then the trace contains a "Ichimoku cloud position" row with status "warn"
    And the trace detail for "Ichimoku cloud position" mentions "78"
