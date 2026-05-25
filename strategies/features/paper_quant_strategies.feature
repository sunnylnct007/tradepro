Feature: Paper-trading bridge for the quant engine
  Translate quant-engine signals into broker-routed orders via the paper
  engine, with a thread-safe trader override registry and broker-agnostic
  router factory. All scenarios use synthetic fixtures — no network.

  # ------------------------------------------------------------------ #
  # Section 1: OverrideRegistry                                          #
  # ------------------------------------------------------------------ #

  Scenario: Fresh registry — strategy is not paused
    Given a fresh OverrideRegistry
    Then is_paused("strat_a") returns False

  Scenario: PAUSE applied — strategy is paused
    Given a fresh OverrideRegistry
    When I apply a PAUSE override for "strat_a"
    Then is_paused("strat_a") returns True

  Scenario: RESUME after PAUSE — strategy is no longer paused
    Given a fresh OverrideRegistry
    When I apply a PAUSE override for "strat_a"
    And I apply a RESUME override for "strat_a"
    Then is_paused("strat_a") returns False

  Scenario: PRICE_OVERRIDE is one-shot — consumed on first read
    Given a fresh OverrideRegistry
    When I apply a PRICE_OVERRIDE for "strat_a" symbol "AAPL" price 199.5
    Then get_price_override "strat_a" "AAPL" returns 199.5
    And get_price_override "strat_a" "AAPL" returns None

  Scenario: SIZE_OVERRIDE is one-shot — consumed on first read
    Given a fresh OverrideRegistry
    When I apply a SIZE_OVERRIDE for "strat_a" symbol "AAPL" quantity 42
    Then get_size_override "strat_a" "AAPL" returns 42
    And get_size_override "strat_a" "AAPL" returns None

  Scenario: VETO_ORDER is one-shot — consume_veto returns True then False
    Given a fresh OverrideRegistry
    When I apply a VETO_ORDER for "strat_a" symbol "AAPL"
    Then consume_veto "strat_a" "AAPL" returns True
    And consume_veto "strat_a" "AAPL" returns False

  Scenario: FORCE_CLOSE is one-shot — consume_force_close returns True then False
    Given a fresh OverrideRegistry
    When I apply a FORCE_CLOSE for "strat_a" symbol "AAPL"
    Then consume_force_close "strat_a" "AAPL" returns True
    And consume_force_close "strat_a" "AAPL" returns False

  Scenario: clear removes all overrides for a strategy
    Given a fresh OverrideRegistry
    When I apply a PAUSE override for "strat_a"
    And I apply a PRICE_OVERRIDE for "strat_a" symbol "AAPL" price 100.0
    And I clear overrides for "strat_a"
    Then is_paused("strat_a") returns False
    And get_price_override "strat_a" "AAPL" returns None

  # ------------------------------------------------------------------ #
  # Section 2: signal_bridge helpers                                     #
  # ------------------------------------------------------------------ #

  Scenario: size_from_vol_target scales down when realised vol exceeds target
    Given a price of 100, capital 10000, target_vol 0.12, realised_vol 0.24, max_leverage 1.5
    When I call size_from_vol_target
    Then the quantity equals 50

  Scenario: size_from_vol_target caps scalar at max_leverage when realised vol is tiny
    Given a price of 100, capital 10000, target_vol 0.12, realised_vol 0.001, max_leverage 1.5
    When I call size_from_vol_target
    Then the quantity equals 150

  Scenario: size_from_vol_target uses neutral sizing when realised vol is None
    Given a price of 100, capital 10000, target_vol 0.12, realised_vol None, max_leverage 1.5
    When I call size_from_vol_target
    Then the quantity equals 100

  Scenario: realised_vol_from_closes returns approximate annualised vol
    Given a synthetic closes series of 252 bars with 1% daily noise
    When I call realised_vol_from_closes
    Then the realised vol is between 0.10 and 0.25

  # ------------------------------------------------------------------ #
  # Section 3: BrokerFactory                                             #
  # ------------------------------------------------------------------ #

  Scenario: create_router t212 returns a T212OrderRouter instance
    When I call create_router with broker "t212"
    Then the result is a T212OrderRouter

  Scenario: create_router with unknown broker raises ValueError
    When I call create_router with broker "unknown"
    Then create_router raises a ValueError

  # ------------------------------------------------------------------ #
  # Section 4: IchimokuEquityStrategy — signal + override                #
  # ------------------------------------------------------------------ #

  Scenario: Paused strategy emits no orders
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I pause the strategy
    And I send one daily bar for "AAPL"
    Then no orders are emitted

  Scenario: Long signal with flat position emits BUY MARKET
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    Then a BUY MARKET order is emitted for "AAPL"
    And the order tag contains "MOO entry"

  Scenario: Flat signal with long position emits SELL MARKET
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with a downtrending feed
    And the strategy already holds 10 shares of "AAPL"
    When I send one daily bar for "AAPL"
    Then a SELL MARKET order is emitted for "AAPL"
    And the order tag contains "MOO exit"

  Scenario: MOO only fires once per session per symbol
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    And I send another daily bar for "AAPL"
    Then the second bar emits no orders

  Scenario: Veto override suppresses the BUY order
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I apply a VETO_ORDER for "AAPL" on the strategy
    And I send one daily bar for "AAPL"
    Then no orders are emitted

  Scenario: Price override converts BUY to LIMIT
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I apply a PRICE_OVERRIDE for "AAPL" price 245.0 on the strategy
    And I send one daily bar for "AAPL"
    Then a BUY LIMIT order is emitted for "AAPL" at 245.0

  Scenario: Size override sets the BUY quantity
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    When I apply a SIZE_OVERRIDE for "AAPL" quantity 7 on the strategy
    And I send one daily bar for "AAPL"
    Then the emitted order has quantity 7

  Scenario: Force-close emits SELL regardless of signal
    Given an IchimokuEquityStrategy bound to symbol "AAPL" with an uptrending feed
    And the strategy already holds 5 shares of "AAPL"
    When I apply a FORCE_CLOSE for "AAPL" on the strategy
    And I send one daily bar for "AAPL"
    Then a SELL MARKET order is emitted for "AAPL"
    And the order tag contains "FORCE_CLOSE"

  # ------------------------------------------------------------------ #
  # Section 5: IchimokuFXMeanReversionStrategy — signal + override       #
  # ------------------------------------------------------------------ #

  Scenario: FX strategy is silent during warmup
    Given an IchimokuFXMeanReversionStrategy bound to pair "EURUSD"
    When I feed 50 random hourly bars for "EURUSD"
    Then no FX orders are emitted

  Scenario: Bearish break after warmup produces a BUY order
    Given an IchimokuFXMeanReversionStrategy bound to pair "EURUSD" with engineered bearish break
    When I drive the strategy to compute its signal
    Then a BUY order is emitted for "EURUSD"

  Scenario: FX strategy paused — no orders on any bar
    Given an IchimokuFXMeanReversionStrategy bound to pair "EURUSD" with engineered bearish break
    When I pause the FX strategy
    And I drive the strategy to compute its signal
    Then no FX orders are emitted

  Scenario: FX force-close emits SELL on a long position
    Given an IchimokuFXMeanReversionStrategy bound to pair "EURUSD"
    And the FX strategy already holds 2 units of "EURUSD"
    When I apply a FORCE_CLOSE for "EURUSD" on the FX strategy
    And I send one hourly bar for "EURUSD"
    Then a SELL FX order is emitted for "EURUSD" with quantity 2

  Scenario: FX size override sets the order quantity
    Given an IchimokuFXMeanReversionStrategy bound to pair "EURUSD" with engineered bearish break
    When I apply a SIZE_OVERRIDE for "EURUSD" quantity 9 on the FX strategy
    And I drive the strategy to compute its signal
    Then the emitted FX order has quantity 9

  # ------------------------------------------------------------------ #
  # Section 6: LLM gate integrated in IchimokuEquityStrategy            #
  # ------------------------------------------------------------------ #

  Scenario: LLM gate disabled — BUY order emits normally
    Given an IchimokuEquityStrategy with a DISABLED LLM gate bound to "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    Then a BUY MARKET order is emitted for "AAPL"

  Scenario: LLM gate VETOED — no BUY order emitted
    Given an IchimokuEquityStrategy with a VETOING LLM gate bound to "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    Then no orders are emitted

  Scenario: LLM gate APPROVED_BOOSTED — order quantity is scaled up
    Given an IchimokuEquityStrategy with a BOOSTING LLM gate bound to "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    Then a BUY MARKET order is emitted for "AAPL"
    And the order quantity is greater than base quantity without boost

  Scenario: LLM gate does not block exits — SELL fires regardless
    Given an IchimokuEquityStrategy with a VETOING LLM gate bound to "AAPL" with a downtrending feed
    And the strategy already holds 10 shares of "AAPL"
    When I send one daily bar for "AAPL"
    Then a SELL MARKET order is emitted for "AAPL"

  Scenario: LLM gate fail_open — order emits when LLM raises an exception
    Given an IchimokuEquityStrategy with an ERROR LLM gate bound to "AAPL" with an uptrending feed
    When I send one daily bar for "AAPL"
    Then a BUY MARKET order is emitted for "AAPL"

  Scenario: Human SIZE_OVERRIDE takes priority over LLM boost scaling
    Given an IchimokuEquityStrategy with a BOOSTING LLM gate bound to "AAPL" with an uptrending feed
    When I apply a SIZE_OVERRIDE for "AAPL" quantity 3 on the strategy
    And I send one daily bar for "AAPL"
    Then the emitted order has quantity 3

  # ------------------------------------------------------------------ #
  # Section 7: LLM gate integrated in IchimokuFXMeanReversionStrategy   #
  # ------------------------------------------------------------------ #

  Scenario: FX LLM gate VETOED — no entry order emitted
    Given an IchimokuFXMeanReversionStrategy with a VETOING LLM gate bound to pair "EURUSD" with engineered bearish break
    When I drive the strategy to compute its signal
    Then no FX orders are emitted

  Scenario: FX LLM gate does not block exits from an existing position
    Given an IchimokuFXMeanReversionStrategy with a VETOING LLM gate bound to pair "EURUSD"
    And the FX strategy already holds 2 units of "EURUSD"
    When I apply a FORCE_CLOSE for "EURUSD" on the FX strategy
    And I send one hourly bar for "EURUSD"
    Then a SELL FX order is emitted for "EURUSD" with quantity 2
