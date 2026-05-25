Feature: Strategy.seed_positions — initialise position state from external snapshot
  Without seeded positions, every rerun of ichimoku_fx_mr computes
  target from a flat _fx_positions {} and re-emits the same entry
  intents, doubling our exposure. paper_session reads the OMS-derived
  net positions on startup and calls strategy.seed_positions so the
  strategy sees what it already holds.

  Scenario: Strategy base seed_positions is a no-op by default
    Given a fresh strategy with bar_buffer_size = 5
    When I seed positions {"AAPL": 7}
    Then no exception is raised
    And the strategy's recent_bars stays empty

  Scenario: ichimoku_fx_mr stores seeded positions in _fx_positions
    Given a fresh ichimoku_fx_mr strategy with warmup_bars = 5
    When I seed positions {"EURUSD": 1, "GBPUSD": -1}
    Then the strategy reports current position EURUSD = 1
    And the strategy reports current position GBPUSD = -1

  Scenario: seeding overrides any prior in-memory state
    Given a fresh ichimoku_fx_mr strategy with warmup_bars = 5
    When I seed positions {"EURUSD": 2}
    And I seed positions {"EURUSD": -3, "USDCHF": 1}
    Then the strategy reports current position EURUSD = -3
    And the strategy reports current position USDCHF = 1
