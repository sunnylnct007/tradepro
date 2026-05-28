Feature: Source-backed bus lookback — pre-fetch warmup bars before session_date
  ichimoku_fx_mr's signal needs ~107 hourly bars before it produces a
  non-zero output, but the daemon's one-day fetch only gives it ~24.
  The bus's lookback_days knob extends the fetch backwards from
  session_date so warmup-hungry strategies can satisfy their gate
  without operator-managed multi-day stitching.

  The lookback window uses business-day candidates (Mon-Fri) plus a
  seven-day buffer so bank holidays never leave the window empty —
  the bus falls back to the nearest available trading day.

  Scenario: single-symbol bus with lookback fetches multi-day window
    Given a stub bar source serving 4 days of EURUSD bars per call
    When I run SourceBackedBus with session_date=2026-05-22 and lookback_days=3
    Then the source is called once per day in the lookback window
    And the bus emits 4 days worth of bars in timestamp order

  Scenario: lookback=0 keeps the current single-day behaviour
    Given a stub bar source serving 4 days of EURUSD bars per call
    When I run SourceBackedBus with session_date=2026-05-22 and lookback_days=0
    Then the source is called exactly once

  Scenario: multi-symbol bus fans lookback across both symbols
    Given a stub bar source serving 2 days of bars per call for EURUSD and GBPUSD
    When I run MultiSymbolSourceBackedBus with session_date=2026-05-22 and lookback_days=1
    Then the source is called twice per symbol
    And the emitted stream contains both EURUSD and GBPUSD bars in timestamp order

  Scenario: bank holiday on lookback day falls back to nearest trading day
    Given a stub bar source where 2026-05-25 returns empty (bank holiday)
    When I run SourceBackedBus with session_date=2026-05-27 and lookback_days=1
    Then the bus still emits 2 days worth of bars in timestamp order
    And data_window_start is set on the bus
    And data_window_start points to a trading day before the holiday
