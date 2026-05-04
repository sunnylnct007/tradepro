Feature: Indicators must use adjusted close (split-aware)
  Splits and large distributions corrupt indicators that read raw
  closing prices. The Python market_state path drives the comparator
  pushed to the API; if anyone ever swaps adj_close back to close,
  this test fails loudly. (.NET SignalEngine keeps the same contract
  via Candle.AdjOrClose — covered in the C# unit tests, follow-up.)

  Scenario: synthetic 4:1 split leaves the 52w-high reading flat
    Given a flat-adjusted price series with a 4:1 split mid-window
    When I compute the market_state for it
    Then the percentage off the 52w high is approximately 0%
    And the drawdown from peak is approximately 0%

  Scenario: raw close series (no adj_close) still works
    Given a flat raw-only price series
    When I compute the market_state for it
    Then the percentage off the 52w high is approximately 0%

  Scenario: INRG-pattern (5y peak old, 52w peak today) does NOT trigger BUY
    Given a 5y price series with a peak in year 1 and a flat recent 12 months at the recovered level
    When I compute the market_state for it
    Then the entry signal is not BUY because of long-term drawdown alone
    And the entry reason does not claim "historical bounce zone" off the 5y peak

  Scenario: 52w-high date + peak date are surfaced for traceability
    Given a price series that peaked 6 months ago and recovered partially
    When I compute the market_state for it
    Then the 52w-high date matches the peak bar
    And the 52w-high price matches the peak value
    And the entry reason mentions the peak date
