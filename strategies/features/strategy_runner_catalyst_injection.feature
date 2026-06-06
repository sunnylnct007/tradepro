Feature: StrategyRunner injects the CatalystFetcher (Phase C-3.3)
  When the runner is constructed with ``catalysts_api_base`` the
  built strategy receives a shared ``_catalyst_fetcher`` via params
  so the C-3.1 LLM-gate overlay fires automatically in production.
  Without an api_base the legacy no-overlay behaviour is preserved.

  Scenario: runner without api_base does not inject the fetcher
    Given a StrategyRunner without a catalysts api_base
    When I build the "intraday_flat" strategy
    Then the built strategy's _catalyst_fetcher is None

  Scenario: runner with api_base injects a shared fetcher
    Given a StrategyRunner with a catalysts api_base "http://api.example"
    When I build the "intraday_flat" strategy
    Then the built strategy's _catalyst_fetcher is not None

  Scenario: same fetcher instance is reused across strategies
    Given a StrategyRunner with a catalysts api_base "http://api.example"
    When I build the "intraday_flat" strategy
    And I build the "ichimoku_equity" strategy
    Then both built strategies share the same _catalyst_fetcher instance

  Scenario: build_catalyst_fetcher returns None when no api_base is set
    Given a StrategyRunner without a catalysts api_base
    When I call build_catalyst_fetcher
    Then the returned fetcher is None

  Scenario: build_catalyst_fetcher returns a non-None instance when configured
    Given a StrategyRunner with a catalysts api_base "http://api.example"
    When I call build_catalyst_fetcher
    Then the returned fetcher is a CatalystFetcher
