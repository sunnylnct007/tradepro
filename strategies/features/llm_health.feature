Feature: Ollama LLM preflight surfaces silent failures
  The sentiment scorer fails silently if Ollama is down OR the
  configured model isn't pulled — both produce empty sentiment
  columns 60s into the backtest with no obvious cause. health_summary
  distinguishes the two states so the operator gets an actionable
  message, not a null column.

  Scenario: daemon down → state daemon_down + actionable message
    Given an Ollama provider that cannot reach the host
    When I get the health summary
    Then the state is "daemon_down"
    And the message tells the user how to start Ollama
    And ok is False

  Scenario: daemon up but model not pulled → state model_missing + pull command
    Given an Ollama provider with daemon up but model "llama3.1:8b" not pulled
    When I get the health summary
    Then the state is "model_missing"
    And the message tells the user to "ollama pull llama3.1:8b"
    And ok is False

  Scenario: daemon up and model present → state ok
    Given an Ollama provider with daemon up and model "llama3.1:8b" available
    When I get the health summary
    Then the state is "ok"
    And ok is True
