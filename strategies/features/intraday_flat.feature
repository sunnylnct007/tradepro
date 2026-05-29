Feature: IntradayFlatStrategy — explainable, risk-averse, EOD-flat
  The intraday_flat strategy picks the day's best Ichimoku longs at the
  open via a scanner, trades them with ATR-anchored stops and an LLM
  news veto, and flatten-closes the book before EOD. Every gate writes
  a structured decision-log entry — the audit trail must answer "why
  did / didn't strategy fire for symbol X today" without reading source.

  All scenarios are synthetic — no Yahoo, no IG, no LLM provider. A
  mocked epic map, synthetic daily DFs, and an injectable LLM gate
  cover the end-to-end paths.

  # ────────────────────────────────────────────────────────────────── #
  # Section 1: Scanner — basket selection                              #
  # ────────────────────────────────────────────────────────────────── #

  Scenario: Scanner ranks candidates by strength and locks top-N
    Given an IntradayFlatStrategy with candidates "SPY,QQQ,IWM" mapped to IG epics
    And synthetic uptrending daily history (IWM strongest drift, QQQ weakest)
    And the regime filter disabled
    When I call on_session_start
    Then the locked basket contains "IWM"
    And the basket has exactly 2 symbols
    And a "basket-selected" decision is logged for "_session"
    And a "basket-rejected-rank" decision is logged for "QQQ"

  Scenario: Scanner drops candidates without an IG epic
    Given an IntradayFlatStrategy with candidates "SPY,UNMAPPED" mapped to IG epics only for "SPY"
    And synthetic uptrending daily history
    And the regime filter disabled
    When I call on_session_start
    Then a "scanner-drop-no-epic" decision is logged for "UNMAPPED"
    And "UNMAPPED" is not in the basket

  Scenario: Scanner drops candidates whose daily Ichimoku signal is flat
    Given an IntradayFlatStrategy with candidates "SPY,QQQ" mapped to IG epics
    And synthetic uptrending daily history for "SPY" and flat history for "QQQ"
    And the regime filter disabled
    When I call on_session_start
    Then a "scanner-drop-no-signal" decision is logged for "QQQ"
    And "QQQ" is not in the basket

  Scenario: Regime BEAR blocks the entire session
    Given an IntradayFlatStrategy with candidates "SPY,QQQ" mapped to IG epics
    And synthetic uptrending daily history for the candidates
    And synthetic DOWNTRENDING history for the regime symbol "SPY"
    And the regime filter enabled
    When I call on_session_start
    Then a "regime-bear-no-trades" decision is logged for "_session"
    And the basket is empty

  # ────────────────────────────────────────────────────────────────── #
  # Section 2: Entry pipeline — gates                                   #
  # ────────────────────────────────────────────────────────────────── #

  Scenario: Off-basket bar is filtered silently
    Given an IntradayFlatStrategy with locked basket "IWM,SPY"
    When I feed one in-window bar for "AAPL"
    Then no orders are emitted
    And no decision is logged for "AAPL"

  Scenario: Bar before the entry window is logged as skip-outside-entry-window
    Given an IntradayFlatStrategy with locked basket "IWM"
    When I feed one PRE-WINDOW bar for "IWM"
    Then no orders are emitted
    And a "skip-outside-entry-window" decision is logged for "IWM"

  Scenario: Second entry on the same day is blocked by one-per-day rule
    Given an IntradayFlatStrategy with locked basket "IWM"
    And the strategy has already emitted an entry for "IWM" this session
    When I feed one in-window bar for "IWM"
    Then no orders are emitted
    And a "skip-one-per-day" decision is logged for "IWM"

  Scenario: Halted risk envelope blocks new entries
    Given an IntradayFlatStrategy with locked basket "IWM"
    And the risk envelope is halted with reason "daily-loss-cap"
    When I feed one in-window bar for "IWM"
    Then no orders are emitted
    And a "skip-halted" decision is logged for "IWM"

  # ────────────────────────────────────────────────────────────────── #
  # Section 3: LLM gate — entries only                                  #
  # ────────────────────────────────────────────────────────────────── #

  Scenario: LLM veto blocks an entry and logs the sentiment reason
    Given an IntradayFlatStrategy with locked basket "IWM" and a VETOING LLM gate
    When I feed one in-window bar for "IWM"
    Then no orders are emitted
    And a "skip-llm-vetoed" decision is logged for "IWM"

  Scenario: LLM boost scales the order quantity up
    Given an IntradayFlatStrategy with locked basket "IWM" and a BOOSTING LLM gate
    When I feed one in-window bar for "IWM"
    Then a BUY MARKET order is emitted for "IWM"
    And the emitted quantity is greater than the unboosted baseline

  # ────────────────────────────────────────────────────────────────── #
  # Section 4: Position management — exits                              #
  # ────────────────────────────────────────────────────────────────── #

  Scenario: Stop-loss exit fires when bar.low breaches the stop price
    Given an IntradayFlatStrategy holding an "IWM" long with stop 198.0 and target 210.0
    When I feed one in-window bar for "IWM" with low 197.5 and high 200.0
    Then a SELL MARKET order is emitted for "IWM"
    And the order tag contains "STOP"
    And a "fire-stop-loss" decision is logged for "IWM"

  Scenario: Take-profit exit fires when bar.high breaches the target
    Given an IntradayFlatStrategy holding an "IWM" long with stop 198.0 and target 210.0
    When I feed one in-window bar for "IWM" with low 200.0 and high 210.5
    Then a SELL MARKET order is emitted for "IWM"
    And the order tag contains "TARGET"
    And a "fire-target" decision is logged for "IWM"

  Scenario: Time-stop exit fires after max_hold_minutes
    Given an IntradayFlatStrategy holding an "IWM" long opened 250 minutes ago
    When I feed one in-window bar for "IWM"
    Then a SELL MARKET order is emitted for "IWM"
    And the order tag contains "TIME"
    And a "fire-time-stop" decision is logged for "IWM"

  # ────────────────────────────────────────────────────────────────── #
  # Section 5: EOD flatten — never LLM-gated                            #
  # ────────────────────────────────────────────────────────────────── #

  Scenario: EOD window flattens all open positions in one pass
    Given an IntradayFlatStrategy holding open longs in "IWM" and "SPY"
    When I feed one EOD-WINDOW bar
    Then SELL MARKET orders are emitted for both "IWM" and "SPY"
    And both order tags contain "EOD"
    And a "fire-eod-flatten" decision is logged for both symbols

  Scenario: EOD flatten is NOT vetoed by the LLM gate
    Given an IntradayFlatStrategy holding an "IWM" long and a VETOING LLM gate
    When I feed one EOD-WINDOW bar for "IWM"
    Then a SELL MARKET order is emitted for "IWM"

  Scenario: on_session_end emits an alert when positions remain open
    Given an IntradayFlatStrategy holding an "IWM" long
    When I call on_session_end without flattening first
    Then an "alert-eod-leftovers" decision is logged for "_session"
    And a "session-summary" decision is logged for "_session"
