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

  # Broker-seed row parsing (paper_session._parse_broker_position_rows).
  # IG FX MINI positions report in mini-lots (|qty| < 1.0); truncating to
  # int gave a FLAT seed, so FX re-sent its full delta every run and stacked
  # duplicate deals. Sign is preserved as +/-1; options/other-universe rows
  # are filtered out.
  Scenario: IG FX mini-lot positions seed as signed +/-1, not truncated to 0
    Given broker rows:
      | ticker               | quantity |
      | CS.D.EURUSD.MINI.IP  | -0.4     |
      | CS.D.AUDUSD.MINI.IP  | -0.7     |
      | CS.D.USDCHF.MINI.IP  | 0.6      |
      | OD.D.WK2EURO.32.IP   | -5       |
    When I parse the broker rows for universe "EURUSD,AUDUSD,USDCHF,USDJPY"
    Then the parsed seed is {"EURUSD": -1, "AUDUSD": -1, "USDCHF": 1}

  Scenario: equity CFD / T212 share quantities keep whole-unit truncation
    Given broker rows:
      | ticker            | quantity |
      | UA.D.AAPL.CASH.IP | 11       |
      | MSFT_US_EQ        | 6.7022   |
    When I parse the broker rows for universe "AAPL,MSFT"
    Then the parsed seed is {"AAPL": 11, "MSFT": 6}

  # Cost basis (averagePricePaid) is captured so the ledger's unrealised P&L
  # = (mark - avg) x qty is real, not mark x qty (~position value). Rows with
  # no/zero broker avg are omitted (ledger leaves their basis untouched).
  Scenario: broker average entry price is captured as cost basis
    Given broker rows:
      | ticker            | quantity | avgPrice |
      | UA.D.AAPL.CASH.IP | 11       | 305.40   |
      | MSFT_US_EQ        | 5        | 426.10   |
      | UC.D.QQQ.CASH.IP  | 6        |          |
    When I parse the broker rows for universe "AAPL,MSFT,QQQ"
    Then the parsed seed is {"AAPL": 11, "MSFT": 5, "QQQ": 6}
    And the parsed cost basis is {"AAPL": 305.40, "MSFT": 426.10}
