Feature: Catalyst sweep universe assembly
  The daily sweep CLI assembles its universe from priority-ordered
  sources (positions → watchlists → trader universe → baseline ETFs
  → baseline FX → extras). Sources upstream win the attribution
  when a symbol appears in multiple lists.

  Scenario: position symbols beat trader universe attribution
    Given positions ["EC", "AAPL"] and watchlists [] and trader_universe ["AAPL", "MSFT"]
    When the universe is assembled
    Then AAPL is attributed to "positions"
    And EC is attributed to "positions"
    And MSFT is attributed to "trader_universe"

  Scenario: empty + whitespace symbols are dropped
    Given positions ["AAPL", "", " ", "MSFT"] and watchlists [] and trader_universe []
    When the universe is assembled
    Then the total symbol count is 2
    And AAPL is attributed to "positions"
    And MSFT is attributed to "positions"

  Scenario: case is normalised so "aapl" and "AAPL" dedupe
    Given positions ["aapl", "AAPL", "Aapl"] and watchlists [] and trader_universe []
    When the universe is assembled
    Then the total symbol count is 1
    And AAPL is attributed to "positions"

  Scenario: baseline ETFs add when flag is on
    Given positions [] and watchlists [] and trader_universe []
    When the universe is assembled with baseline_etfs on and baseline_fx off
    Then the source "baseline_etfs" exists
    And the source "baseline_fx" does not exist

  Scenario: ordering is deterministic (sorted within each source)
    Given positions ["ZZZ", "AAA", "MMM"] and watchlists [] and trader_universe []
    When the universe is assembled with baselines off
    Then the symbol order is "AAA MMM ZZZ"
