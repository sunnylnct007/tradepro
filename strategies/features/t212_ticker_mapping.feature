Feature: T212 ticker mapping — equities vs FX vs unsupported
  The _to_t212_ticker helper used to slap _US_EQ onto every alnum
  symbol, including FX pairs. That meant ichimoku_fx_mr's EURUSD
  intents landed on T212 as "EURUSD_US_EQ" — an instrument T212 has
  no concept of, so 200+ pending orders piled up unable to approve.
  Pin the contract: G10 FX uses the FX mapping table, US equities
  keep the suffix path, anything else raises.

  Scenario: US equity symbols keep the legacy _US_EQ suffix
    When I map the T212 ticker for "AAPL"
    Then the T212 ticker is "AAPL_US_EQ"

  Scenario: another US equity
    When I map the T212 ticker for "MSFT"
    Then the T212 ticker is "MSFT_US_EQ"

  Scenario: G10 FX pair uses the FX mapping table, not _US_EQ
    When I map the T212 ticker for "EURUSD"
    Then the T212 ticker is "EURUSD"

  Scenario: G10 FX pair USDJPY
    When I map the T212 ticker for "USDJPY"
    Then the T212 ticker is "USDJPY"

  Scenario: FX mapping is case-insensitive
    When I map the T212 ticker for "eurusd"
    Then the T212 ticker is "EURUSD"

  Scenario: already-T212-shaped tickers pass through untouched
    When I map the T212 ticker for "AAPL_US_EQ"
    Then the T212 ticker is "AAPL_US_EQ"

  Scenario: unmapped non-US symbol raises loudly
    When I map the T212 ticker for "VOD.L"
    Then a ValueError is raised mentioning "not configured"
