Feature: Intraday + swing exit framework
  Implements the mandatory exit triad and position sizing from
  IMPROVEMENT_SUGGESTIONS_v1.md §3 + SIGNAL_CARD_SPEC_v1.md §3.
  ATR-adjusted stops scale to instrument volatility; position size
  derives from stop distance, not the reverse; the RR gate refuses
  anything below 2:1 (would need >50% win rate to be net positive).

  # ─────────── compute_exit_levels ───────────

  Scenario: ATR_14 = 3.21 with momentum strategy_type
    Given an entry price of 213.50
    And atr_14 is 3.21
    And the strategy_type is "momentum"
    When I compute exit levels
    Then the method is "ATR_ADJUSTED"
    And the stop_loss is approximately 208.685
    And the take_profit is approximately 223.13
    And the rr_ratio is 2.0

  Scenario: ATR-adjusted scales differently for a low-vol name
    Given an entry price of 213.50
    And atr_14 is 1.20
    And the strategy_type is "momentum"
    When I compute exit levels
    Then the method is "ATR_ADJUSTED"
    And the stop_loss is approximately 211.70
    And the take_profit is approximately 217.10

  Scenario: missing ATR falls back to fixed-pct momentum defaults
    Given an entry price of 100.00
    And atr_14 is None
    And the strategy_type is "momentum"
    When I compute exit levels
    Then the method is "FIXED_PCT"
    And the stop_loss is approximately 98.50
    And the take_profit is approximately 103.00
    And the rr_ratio is 2.0

  Scenario: missing ATR falls back to fixed-pct mean_reversion defaults (tighter)
    Given an entry price of 100.00
    And atr_14 is None
    And the strategy_type is "mean_reversion"
    When I compute exit levels
    Then the method is "FIXED_PCT"
    And the stop_loss is approximately 99.00
    And the take_profit is approximately 102.00

  Scenario: unknown strategy_type falls back to momentum defaults
    Given an entry price of 100.00
    And atr_14 is None
    And the strategy_type is "made_up"
    When I compute exit levels
    Then the method is "FIXED_PCT"
    And the stop_loss is approximately 98.50

  Scenario: zero entry price returns nothing
    Given an entry price of 0.0
    And atr_14 is 3.0
    And the strategy_type is "momentum"
    When I compute exit levels
    Then there are no exit levels

  # ─────────── gate_check_rr ───────────

  Scenario: RR 2.0 passes the gate
    Given an entry price of 100.00
    And atr_14 is 2.0
    And the strategy_type is "momentum"
    When I compute exit levels
    And I check the RR gate
    Then the gate passes

  Scenario: an RR-1.0 setup fails the gate
    Given an entry price of 100.00 with a custom rr_ratio of 1.0
    And atr_14 is 2.0
    And the strategy_type is "momentum"
    When I compute exit levels
    And I check the RR gate
    Then the gate fails
    And the gate reason mentions "below floor"

  # ─────────── compute_position_sizing ───────────

  Scenario: £10000 account, 1% risk, $1.50 stop distance
    Given an account size of 10000 GBP with 1.0 percent risk per trade
    And the entry price is 100.00 USD
    And the stop distance is 1.50 USD
    And the FX rate is 1.25 GBPUSD
    When I compute position sizing
    Then the suggested shares is 83
    And the max_loss_gbp is approximately 100.00

  Scenario: account too small to take any position
    Given an account size of 100 GBP with 1.0 percent risk per trade
    And the entry price is 100.00 USD
    And the stop distance is 10.00 USD
    And the FX rate is 1.25 GBPUSD
    When I compute position sizing
    Then there is no position sizing

  Scenario: zero stop distance returns nothing
    Given an account size of 10000 GBP with 1.0 percent risk per trade
    And the entry price is 100.00 USD
    And the stop distance is 0.0 USD
    And the FX rate is 1.25 GBPUSD
    When I compute position sizing
    Then there is no position sizing
