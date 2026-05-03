Feature: Session trace auto-records every MCP tool call
  As a careful operator I want every Claude-Desktop-driven Q&A
  audited automatically — not only when the LLM cooperates by
  calling begin_trace. The session trace is the safety net.

  Scenario: instrumented decorator records a successful call
    Given a fresh session trace
    When I call an instrumented tool that returns a JSON _source citation
    Then the session trace gains one tool_call step
    And the recorded step captures the latency and the citation _source
    And the recorded step has no error

  Scenario: instrumented decorator records a failing call
    Given a fresh session trace
    When I call an instrumented tool that raises an exception
    Then the session trace gains one tool_call step
    And the recorded step has an error matching the exception
    And the original exception still propagates to the caller

  Scenario: full-output mode bypasses the lean summary
    Given a fresh session trace with TRADEPRO_TRACE_FULL=1
    When I call an instrumented tool that returns a large payload
    Then the recorded step retains the full parsed payload
