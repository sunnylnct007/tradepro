Feature: MCP tools for the trustworthy bar cache (Phase B-4)
  Expose the bar cache's read paths + safe get_bars to AI agents
  via the same FastMCP pattern as get_compare / get_portfolio /
  evaluate_symbols. Scenarios stub HTTP so no network is needed.

  Scenario: bar_cache_list_asset_classes returns the registered plugins
    When I call tools.bar_cache_list_asset_classes
    Then the response is ok
    And the response _source is "tradepro://bar-cache/asset_classes"
    And the response asset_classes includes a plugin named "us_etf"
    And the us_etf plugin schema_version is "us_equity_v1"

  Scenario: bar_cache_list_providers returns the registered providers
    When I call tools.bar_cache_list_providers
    Then the response is ok
    And the response _source is "tradepro://bar-cache/providers"
    And the response providers includes a provider named "yfinance"
    And the yfinance provider documents the 1m resolution

  Scenario: bar_cache_health returns rows from the API
    Given a fake bar-cache API returning a health row for "SPY" in "us_etf"
    When I call tools.bar_cache_health filtered by canonical "SPY"
    Then the response is ok
    And the response count is 1
    And the response rows[0] canonical is "SPY"

  Scenario: bar_cache_events surfaces recent telemetry
    Given a fake bar-cache API returning 2 fetch events for "SPY"
    When I call tools.bar_cache_events filtered by canonical "SPY" with limit 10
    Then the response is ok
    And the response count is 2
    And the response events[0] result is "complete"

  Scenario: bar_cache_provider_preferences honours filters client-side
    Given a fake bar-cache API returning preferences for us_etf/1m and us_etf/1d
    When I call tools.bar_cache_provider_preferences for asset_class "us_etf" resolution "1m"
    Then the response is ok
    And the response count is 1
    And the response preferences[0] resolution is "1m"

  Scenario: bar_cache_get_bars surfaces a structured error envelope on partial coverage
    Given a fake yfinance provider returning bars only for 2024-12-23
    And a fresh bar cache base directory at the home location is unavailable
    When I call tools.bar_cache_get_bars for SPY us_etf 1m 2024-12-02 to 2024-12-31 without allow_partial
    Then the response is not ok
    And the response error_class is "partial_coverage"
    And the response retry_strategy is "user_intervention"

  Scenario: bar_cache_get_bars completes when allow_partial is set
    Given a fake yfinance provider returning bars only for 2024-12-23
    When I call tools.bar_cache_get_bars for SPY us_etf 1m 2024-12-02 to 2024-12-31 with allow_partial
    Then the response is ok
    And the response summary coverage_complete is False
    And the response summary rows_returned is 390
