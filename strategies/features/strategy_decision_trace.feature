Feature: Per-bar decision trace — answer "why didn't strategy X trade?"
  Strategies record a short rationale at every gate point in on_bar so
  the UI can render a "why no fill" trace per session without forcing
  the operator to grep server logs.

  Scenario: ichimoku_fx_mr logs skip-warmup on early bars
    Given a fresh ichimoku_fx_mr strategy with warmup_bars = 5
    When I feed it 3 EURUSD bars before it warms up
    Then its decision trace contains 3 "skip-warmup" entries for EURUSD
    And each skip-warmup entry carries bars_seen and bars_required in its detail

  Scenario: ichimoku_fx_mr logs a non-trivial decision once warm
    Given a fresh ichimoku_fx_mr strategy with warmup_bars = 5
    When I feed it 12 EURUSD bars
    Then its decision trace contains at least one non-warmup entry for EURUSD

  Scenario: snapshot decisions field is populated and JSON-serialisable
    Given an engine wired with an ichimoku_fx_mr strategy that has logged a skip-warmup decision
    When I take a ledger snapshot via engine.attach_decisions
    Then the snapshot's strategy entry has a populated "decisions" list
    And the snapshot round-trips through json.dumps without error
