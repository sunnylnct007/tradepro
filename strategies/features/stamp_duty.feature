Feature: Per-symbol UK stamp duty resolution
  UK SDRT is 0.5% on LSE main-market share buys, but UCITS ETFs are
  exempt and non-UK securities pay no UK SDRT at all. Applying a
  flat rate across a mixed run silently penalises high-turnover
  strategies on ETFs and shifts the Sharpe ranking. The fees module
  is the single source of truth so users can't get this wrong.

  Scenario: known UCITS ETFs return 0% regardless of venue suffix
    Given the symbols VWRP.L, SWDA.L, VUKE.L, INRG.L
    When I resolve their stamp duty rates
    Then every rate is 0%

  Scenario: US-listed ETFs in our watchlists also return 0%
    Given the symbols VOO, QQQ, GLD, SPY
    When I resolve their stamp duty rates
    Then every rate is 0%

  Scenario: LSE main-market shares not in any ETF watchlist return 0.5%
    Given the symbols BARC.L, LLOY.L, HSBA.L, SHEL.L
    When I resolve their stamp duty rates
    Then every rate is 0.5%

  Scenario: Non-UK shares pay no UK SDRT
    Given the symbols AAPL, NVDA, MSFT
    When I resolve their stamp duty rates
    Then every rate is 0%

  Scenario: stamp_duty_summary groups a mixed basket by rate
    Given the symbols VWRP.L, BARC.L, AAPL, SWDA.L, HSBA.L
    When I summarise stamp duty for the basket
    Then 3 symbols are in the 0% group
    And 2 symbols are in the 0.5% group
