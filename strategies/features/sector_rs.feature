Feature: Sector relative strength

  12-week price return of a symbol vs its sector ETF proxy.
  A positive RS means the stock is outperforming its sector;
  negative means it's lagging. The 0–10 score feeds COMPASS.

  All scenarios that touch prices use mocked price returns — no
  network calls. Curated SYMBOL_SECTOR_ETF map lookups are tested
  directly (pure dict, zero latency).

  # ──────────────────────────────────────────────────────────────────
  # _rs_to_score — pure mapping function
  # ──────────────────────────────────────────────────────────────────

  Scenario Outline: _rs_to_score maps RS to correct bucket
    When I call _rs_to_score with rs_pct=<rs>
    Then the score is <expected>

    Examples:
      | rs     | expected |
      | 20.0   | 10       |
      | 15.0   | 10       |
      | 14.9   | 9        |
      | 8.0    | 9        |
      | 7.9    | 7        |
      | 4.0    | 7        |
      | 3.9    | 6        |
      | 1.0    | 6        |
      | 0.5    | 5        |
      | 0.0    | 5        |
      | -0.5   | 5        |
      | -1.0   | 5        |
      | -1.1   | 4        |
      | -4.0   | 4        |
      | -4.1   | 3        |
      | -8.0   | 3        |
      | -8.1   | 2        |
      | -15.0  | 2        |
      | -15.1  | 1        |
      | -30.0  | 1        |

  # ──────────────────────────────────────────────────────────────────
  # get_sector_etf — curated map lookups (no yfinance)
  # ──────────────────────────────────────────────────────────────────

  Scenario Outline: Curated SYMBOL_SECTOR_ETF map returns correct ETF
    When I call get_sector_etf for "<symbol>"
    Then the ETF is "<etf>"
    And fallback is False

    Examples:
      | symbol   | etf  |
      | NVDA     | SOXX |
      | MU       | SOXX |
      | ASML     | SOXX |
      | AMD      | SOXX |
      | AAPL     | XLK  |
      | MSFT     | XLK  |
      | META     | XLC  |
      | NFLX     | XLC  |
      | JPM      | XLF  |
      | HSBA.L   | EWU  |
      | AZN.L    | EWU  |
      | VUKE.L   | SPY  |
      | BTC-USD  | BTC-USD |

  Scenario: Symbol is normalised to uppercase before lookup
    When I call get_sector_etf for "nvda"
    Then the ETF is "SOXX"
    And fallback is False

  Scenario: Unknown symbol returns SPY with fallback=True
    When I call get_sector_etf for "UNKNOWNTICKER999" via yfinance stub returning no sector
    Then the ETF is "SPY"
    And fallback is True

  # ──────────────────────────────────────────────────────────────────
  # compute_sector_rs — neutral / error paths (no network)
  # ──────────────────────────────────────────────────────────────────

  Scenario: Symbol equal to its own sector ETF returns neutral rs_score=5
    When I call compute_sector_rs for "SPY"
    Then rs_score is 5
    And error is not None

  Scenario: BTC-USD is its own benchmark — neutral result
    When I call compute_sector_rs for "BTC-USD"
    Then rs_score is 5

  Scenario: Price fetch failure returns neutral rs_score=5
    Given _price_return is mocked to return None
    When I call compute_sector_rs for "AAPL"
    Then rs_score is 5
    And error is not None

  Scenario: Strong outperformer produces high score
    Given _price_return returns 25.0 for "NVDA" and 9.0 for "SOXX"
    When I call compute_sector_rs for "NVDA"
    Then rs_12w_pct is approximately 16.0
    And rs_score is 10
    And symbol_12w_pct is 25.0
    And etf_12w_pct is 9.0

  Scenario: Strong underperformer produces low score
    Given _price_return returns 2.0 for "INTC" and 18.0 for "SOXX"
    When I call compute_sector_rs for "INTC"
    Then rs_12w_pct is approximately -16.0
    And rs_score is 1

  Scenario: compute_sector_rs result contains all required keys
    Given _price_return returns 5.0 for "AAPL" and 3.0 for "XLK"
    When I call compute_sector_rs for "AAPL"
    Then the result contains keys: symbol, sector_etf, fallback, symbol_12w_pct, etf_12w_pct, rs_12w_pct, rs_score, as_of, error
