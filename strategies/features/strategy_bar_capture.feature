Feature: Bars-seen capture — answer "what data did the strategy receive?"
  The paper engine calls strategy.record_bar(bar) before strategy.on_bar(bar)
  so the snapshot's bars_seen list mirrors exactly the bars each strategy
  processed. The UI uses this to render a Bars data-frame alongside the
  decisions trace, closing the "black box" gap.

  Scenario: strategy records every bar it sees
    Given a fresh strategy with bar_buffer_size = 10
    When I feed it 5 AAPL bars via record_bar
    Then its recent_bars returns 5 entries
    And each entry carries ts, symbol, open, high, low, close, volume

  Scenario: ring buffer caps per symbol
    Given a fresh strategy with bar_buffer_size = 3
    When I feed it 10 AAPL bars via record_bar
    Then its recent_bars returns 3 entries
    And the entries are the last 3 bars in timestamp order

  Scenario: bars from different symbols stay isolated
    Given a fresh strategy with bar_buffer_size = 50
    When I feed it 4 AAPL bars and 6 MSFT bars via record_bar
    Then its recent_bars returns 10 entries
    And entries are time-ordered ascending
    And AAPL appears 4 times and MSFT appears 6 times

  Scenario: snapshot bars_seen is populated and JSON-serialisable
    Given an engine wired with a strategy that has recorded a few bars
    When I take a ledger snapshot via engine.attach_bars
    Then the snapshot's strategy entry has a populated "bars_seen" list
    And the snapshot round-trips through json.dumps without error

  Scenario: engine auto-records bars during a session
    Given an engine running a replay session with 4 AAPL bars
    When the session completes
    Then the strategy's bars_seen contains 4 entries for AAPL
