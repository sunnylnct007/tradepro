Feature: IBKR bar-cache provider — historical OHLCV from Interactive Brokers
  IBKRProvider plugs into the BarStore provider chain and harvests
  historical bars via ib_insync. The provider must:
    * Correctly map BarStore resolutions to IBKR barSizeSetting strings.
    * Split long date ranges into IBKR-max-chunk-size windows and
      concatenate the results in chronological order.
    * Normalise ib_insync BarData to the bar_cache column schema
      (lowercase OHLCV + adj_factor=1.0 + source="ibkr").
    * Surface typed BarFetchError subclasses so the BarStore chain can
      fall through to the next provider (yfinance, IG) on failure.
    * Cache the gateway connection across fetch calls — never re-connect
      per request (pacing + anti-abuse guard).
    * Register itself as "ibkr" in the global provider registry at
      import time so it's resolvable by the BarStore chain.

  All scenarios use a synthetic _fetch_fn so no real TWS / IB Gateway
  is needed. The mock captures every call so we can assert chunk count,
  bar_size, durationStr, and whatToShow parameters.

  Background:
    Given the IBKRProvider registry is cleared for a clean test

  # ──────────────────────────────────────────────────────────────────
  # Section 1: Resolution and bar_size mapping
  # ──────────────────────────────────────────────────────────────────

  Scenario: 1m resolution maps to "1 min" barSizeSetting
    Given an IBKRProvider with a mock that returns 390 bars
    When I fetch "SPY" us_etf 1m bars for one day
    Then the mock received barSizeSetting "1 min"
    And the result has 390 rows

  Scenario: 5m resolution maps to "5 mins" barSizeSetting
    Given an IBKRProvider with a mock that returns 78 bars
    When I fetch "SPY" us_etf 5m bars for one day
    Then the mock received barSizeSetting "5 mins"
    And the result has 78 rows

  Scenario: 1d resolution maps to "1 day" barSizeSetting
    Given an IBKRProvider with a mock that returns 22 bars
    When I fetch "SPY" us_etf 1d bars for one month
    Then the mock received barSizeSetting "1 day"
    And the result has 22 rows

  Scenario: Unsupported resolution raises ProviderParseError
    Given an IBKRProvider with a mock that returns 0 bars
    When I fetch "SPY" us_etf "3m" bars for one day
    Then a ProviderParseError is raised

  # ──────────────────────────────────────────────────────────────────
  # Section 2: Asset class → whatToShow mapping
  # ──────────────────────────────────────────────────────────────────

  Scenario: us_equity uses "TRADES" whatToShow
    Given an IBKRProvider with a mock that returns 390 bars
    When I fetch "AAPL" us_equity 1m bars for one day
    Then the mock received whatToShow "TRADES"

  Scenario: forex uses "MIDPOINT" whatToShow
    Given an IBKRProvider with a mock that returns 390 bars
    When I fetch "EURUSD" forex 1m bars for one day
    Then the mock received whatToShow "MIDPOINT"

  Scenario: whatToShow can be overridden at construction time
    Given an IBKRProvider with what_to_show="BID" and a mock returning 10 bars
    When I fetch "AAPL" us_equity 1m bars for one day
    Then the mock received whatToShow "BID"

  # ──────────────────────────────────────────────────────────────────
  # Section 3: Range chunking
  # ──────────────────────────────────────────────────────────────────

  Scenario: A 1-day request for 1m bars sends exactly 1 chunk
    Given an IBKRProvider with a mock that returns 390 bars
    When I fetch "SPY" us_etf 1m bars for one day
    Then the mock was called exactly 1 time

  Scenario: A 35-day request for 1m bars is split into 2 chunks
    Given an IBKRProvider with a mock that returns 50 bars per chunk
    When I fetch "SPY" us_etf 1m bars for 35 days
    Then the mock was called exactly 2 times
    And the result has 100 rows

  Scenario: Chunks are concatenated in chronological order
    Given an IBKRProvider with a mock returning ascending timestamps across 2 chunks
    When I fetch "SPY" us_etf 1m bars for 35 days
    Then the returned DataFrame index is strictly increasing

  Scenario: Duplicate bars at chunk boundaries are deduplicated
    Given an IBKRProvider with a mock that returns an overlapping bar at the chunk boundary
    When I fetch "SPY" us_etf 1m bars for 35 days
    Then the result contains no duplicate timestamps

  # ──────────────────────────────────────────────────────────────────
  # Section 4: Output schema
  # ──────────────────────────────────────────────────────────────────

  Scenario: Returned DataFrame has the canonical bar_cache columns
    Given an IBKRProvider with a mock that returns 5 well-formed bars
    When I fetch "SPY" us_etf 1m bars for one day
    Then the result columns include open, high, low, close, volume
    And the result has adj_factor column equal to 1.0 for all rows
    And the result has source column equal to "ibkr" for all rows
    And the result index is tz-aware UTC

  Scenario: Empty response from mock returns an empty DataFrame (no error)
    Given an IBKRProvider with a mock that returns 0 bars
    When I fetch "SPY" us_etf 1m bars for one day
    Then the result has 0 rows and no error is raised

  # ──────────────────────────────────────────────────────────────────
  # Section 5: Error surface — chain fallthrough
  # ──────────────────────────────────────────────────────────────────

  Scenario: Connection failure raises ProviderNetworkError
    Given an IBKRProvider whose mock raises a connection error
    When I fetch "SPY" us_etf 1m bars for one day
    Then a ProviderNetworkError is raised with provider "ibkr"

  Scenario: Pacing violation raises ProviderRateLimitError
    Given an IBKRProvider whose mock raises a pacing violation
    When I fetch "SPY" us_etf 1m bars for one day
    Then a ProviderRateLimitError is raised with provider "ibkr"

  # ──────────────────────────────────────────────────────────────────
  # Section 6: max_history and supports_resolution
  # ──────────────────────────────────────────────────────────────────

  Scenario: 1m resolution max_history is 365 days
    Given an IBKRProvider with a mock that returns 0 bars
    Then the max_history for resolution "1m" is 365 days

  Scenario: 1d resolution max_history is None (unlimited)
    Given an IBKRProvider with a mock that returns 0 bars
    Then the max_history for resolution "1d" is None

  Scenario: "3m" resolution is not supported
    Given an IBKRProvider with a mock that returns 0 bars
    Then the provider does not support resolution "3m"

  Scenario: All standard resolutions are supported
    Given an IBKRProvider with a mock that returns 0 bars
    Then the provider supports resolutions 1m, 5m, 15m, 30m, 1h, 1d

  # ──────────────────────────────────────────────────────────────────
  # Section 7: Registry
  # ──────────────────────────────────────────────────────────────────

  Scenario: IBKRProvider is registered as "ibkr" in the global registry
    When I import the bar_cache providers module
    Then "ibkr" is resolvable via get_provider
