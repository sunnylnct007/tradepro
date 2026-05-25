Feature: LLM signal gate, strategy config registry, and runner façade
  Pluggable LLM approval layer in front of paper order emission,
  JSON-backed per-strategy config, and a runner façade that assembles
  strategies from stored config. All scenarios use synthetic fixtures —
  no network, no real LLM provider.

  # ------------------------------------------------------------------ #
  # Section A: LLMGateConfig                                             #
  # ------------------------------------------------------------------ #

  Scenario: Default LLMGateConfig has conservative thresholds
    Given a default LLMGateConfig
    Then the gate config is enabled
    And the veto threshold is -0.4
    And the boost threshold is 0.5
    And fail_open is True

  Scenario: LLMGateConfig round-trips through dict
    Given a default LLMGateConfig
    When I serialize the config to a dict and back
    Then the round-tripped config equals the original

  Scenario: Disabled gate always APPROVES regardless of sentiment
    Given an LLMSignalGate with enabled=False
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED"
    And the decision scale_factor is 1.0

  # ------------------------------------------------------------------ #
  # Section B: GateDecision logic with direct inputs                     #
  # ------------------------------------------------------------------ #

  Scenario: Signal of zero is always APPROVED (no position to gate)
    Given an LLMSignalGate with default config
    When I evaluate symbol "AAPL" signal 0.0
    Then the decision action is "APPROVED"
    And the decision reason mentions "no position"

  Scenario: Sentiment well below veto threshold with material article triggers VETO
    Given an LLMSignalGate with injected news of 1 headline
    And the injected scorer returns sentiments [-0.6] with material [True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "VETOED"
    And the decision scale_factor is 0.0

  Scenario: Sentiment above boost threshold triggers APPROVED_BOOSTED
    Given an LLMSignalGate with injected news of 1 headline
    And the injected scorer returns sentiments [0.7] with material [True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED_BOOSTED"
    And the decision scale_factor is 1.25

  Scenario: Sentiment between thresholds is APPROVED with scale 1.0
    Given an LLMSignalGate with injected news of 1 headline
    And the injected scorer returns sentiments [-0.2] with material [True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED"
    And the decision scale_factor is 1.0

  Scenario: LLM error with fail_open=True returns APPROVED, never blocks trading
    Given an LLMSignalGate that always raises on scoring
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED"
    And the decision reason mentions "llm_error"

  # ------------------------------------------------------------------ #
  # Section C: LLMSignalGate with injected news/score                    #
  # ------------------------------------------------------------------ #

  Scenario: No headlines returned by news fetcher means APPROVED with 0 checked
    Given an LLMSignalGate with no headlines
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED"
    And the decision headlines_checked is 0

  Scenario: Three uniformly negative material headlines VETO
    Given an LLMSignalGate with injected news of 3 headlines
    And the injected scorer returns sentiments [-0.6, -0.6, -0.6] with material [True, True, True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "VETOED"

  Scenario: Three uniformly positive material headlines BOOST
    Given an LLMSignalGate with injected news of 3 headlines
    And the injected scorer returns sentiments [0.7, 0.7, 0.7] with material [True, True, True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED_BOOSTED"
    And the decision scale_factor is 1.25

  Scenario: Three mixed headlines averaging zero are APPROVED at 1.0x
    Given an LLMSignalGate with injected news of 3 headlines
    And the injected scorer returns sentiments [-0.3, 0.0, 0.3] with material [True, True, True]
    When I evaluate symbol "AAPL" signal 1.0
    Then the decision action is "APPROVED"
    And the decision scale_factor is 1.0

  # ------------------------------------------------------------------ #
  # Section D: StrategyConfigRegistry                                    #
  # ------------------------------------------------------------------ #

  Scenario: Unknown strategy returns a sensible default config
    Given a fresh StrategyConfigRegistry
    When I get the config for "unknown_strategy"
    Then the config is enabled
    And the config params dict is empty
    And the config llm_gate is the default gate config

  Scenario: Config set then retrieved persists across reads
    Given a fresh StrategyConfigRegistry
    When I update params for "ichimoku_equity" with {"sleeve_size": 25, "target_vol": 0.15}
    Then the stored params for "ichimoku_equity" equal {"sleeve_size": 25, "target_vol": 0.15}

  Scenario: update_params merges — existing keys preserved, new keys added
    Given a fresh StrategyConfigRegistry
    When I update params for "ichimoku_equity" with {"sleeve_size": 25}
    And I update params for "ichimoku_equity" with {"target_vol": 0.15}
    Then the stored params for "ichimoku_equity" contain key "sleeve_size" with value 25.0
    And the stored params for "ichimoku_equity" contain key "target_vol" with value 0.15

  Scenario: update_llm_gate updates the gate and to_status_dict reflects it
    Given a fresh StrategyConfigRegistry
    When I update the LLM gate for "ichimoku_equity" with enabled=False
    Then to_status_dict for "ichimoku_equity" shows llm_gate enabled False

  # ------------------------------------------------------------------ #
  # Section E: StrategyRunner                                            #
  # ------------------------------------------------------------------ #

  Scenario: Disabled strategy is excluded from get_active_strategies
    Given a fresh StrategyRunner
    When I configure strategy "strat_a" enabled True
    And I configure strategy "strat_b" enabled False
    Then get_active_strategies includes "strat_a"
    And get_active_strategies does not include "strat_b"

  Scenario: Paused strategy is excluded from get_active_strategies
    Given a fresh StrategyRunner
    When I configure strategy "strat_a" enabled True
    And I pause strategy "strat_a" via the override registry
    Then get_active_strategies does not include "strat_a"

  Scenario: build_strategy returns None for an unknown strategy name
    Given a fresh StrategyRunner
    When I call build_strategy for "no_such_strategy"
    Then the build result is None

  Scenario: status dict exposes all required keys per strategy
    Given a fresh StrategyRunner
    When I configure strategy "strat_a" enabled True
    Then the status row for "strat_a" has keys strategy_name, enabled, paused, llm_gate_enabled
