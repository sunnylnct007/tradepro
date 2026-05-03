Feature: Dispersion-first event analysis (no false unanimity)
  When the user asks "what's the impact of <event>?" the MCP layer
  must surface dispersion across uncorrelated proxies — not three
  near-identical broad-market ETFs that share 70%+ of constituents
  and will always agree. Pin the macro_proxies watchlist + the
  get_returns shape + the period-reference walker.

  Scenario: etf_macro_proxies watchlist contains the right axes
    When I resolve the etf_macro_proxies watchlist
    Then it includes risk-on equity proxies (SPY, QQQ, EFA, EEM)
    And it includes risk-off proxies (TLT, AGG, GLD)
    And it includes a commodity proxy (USO)
    And it includes a sector / event proxy (XLE, ITA)
    And it includes a volatility proxy (VIXY)

  Scenario: etf_all union now includes the macro proxies
    When I resolve the etf_all watchlist
    Then it contains SPY
    And it contains GLD
    And it contains VIXY

  Scenario: every macro proxy carries an axis label (no orphans)
    When I resolve the etf_macro_proxies watchlist
    Then every symbol has a macro_axis label
    And every axis member appears in the watchlist

  Scenario: get_returns walks back over weekends to a real bar
    Given a daily price series ending on a Friday
    When I look up the 1d reference price for the basket
    Then the reference price is the prior trading day's close
    And the lookup never returns a NaN for a present series
