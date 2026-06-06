Feature: Symbol → asset_class resolver
  The backtest CLIs derive the BarStore plugin per symbol from a
  ticker-suffix rule set (no DB lookup on the hot path). Five
  plugins; unknown for unparseable input.

  Scenario Outline: ticker resolves to expected asset class
    When the resolver is asked for "<ticker>"
    Then the asset class is "<expected>"

    Examples: US single-name equities
      | ticker | expected  |
      | AAPL   | us_equity |
      | MSFT   | us_equity |
      | NVDA   | us_equity |
      | aapl   | us_equity |
      | TSLA   | us_equity |

    Examples: US ETFs (override list)
      | ticker | expected  |
      | SPY    | us_etf    |
      | QQQ    | us_etf    |
      | IWM    | us_etf    |
      | GLD    | us_etf    |
      | XLF    | us_etf    |
      | TLT    | us_etf    |
      | ARKK   | us_etf    |

    Examples: UK equities (LSE suffix)
      | ticker | expected   |
      | BARC.L | uk_equity  |
      | VOD.L  | uk_equity  |
      | HSBA.L | uk_equity  |

    Examples: FX pairs (Yahoo =X format)
      | ticker    | expected |
      | EURUSD=X  | fx_spot  |
      | GBPUSD=X  | fx_spot  |
      | USDJPY=X  | fx_spot  |

    Examples: Crypto (Yahoo -USD/-USDT/-EUR formats)
      | ticker    | expected |
      | BTC-USD   | crypto   |
      | ETH-USD   | crypto   |
      | SOL-USDT  | crypto   |
      | BTC-EUR   | crypto   |
      | ETH-USDC  | crypto   |

  Scenario: whitespace-only ticker is unknown
    When the resolver is asked for "   "
    Then the asset class is "unknown"

  Scenario: None input is unknown
    When the resolver is asked for None
    Then the asset class is "unknown"

  Scenario: resolve_many returns one entry per input ticker
    When the resolver runs on tickers AAPL SPY EURUSD=X BARC.L BTC-USD
    Then the resolver result has 5 entries
    And the mapping AAPL is "us_equity"
    And the mapping SPY is "us_etf"
    And the mapping EURUSD=X is "fx_spot"
    And the mapping BARC.L is "uk_equity"
    And the mapping BTC-USD is "crypto"
