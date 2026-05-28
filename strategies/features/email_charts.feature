Feature: email_charts — chart helpers for the daily HTML digest
  The email body embeds three matplotlib PNGs as base64 data URLs.
  Failure mode is silent: a chart that can't render returns "" and
  the HTML omits the <img>. Pin that contract so a future refactor
  doesn't crash the worker mid-send.

  Scenario: bucket donut returns "" when every bucket is zero
    Given bucket counts buy=0 wait=0 avoid=0
    When I render the bucket donut
    Then the data URL is empty

  Scenario: bucket donut renders a PNG when any bucket is non-zero
    Given bucket counts buy=2 wait=1 avoid=3
    When I render the bucket donut
    Then the data URL is a base64 PNG

  Scenario: BUY sparklines read recent_closes when present on the item
    Given one BUY item "VUKE.L" with recent_closes of 30 ascending floats
    When I render the BUY sparklines
    Then the data URL is a base64 PNG

  Scenario: BUY sparklines fall back to market_state.closes_30d
    Given one BUY item "QQQ" with no recent_closes but market_state.closes_30d of 30 floats
    When I render the BUY sparklines
    Then the data URL is a base64 PNG

  Scenario: BUY sparklines return "" when no symbol has a usable series
    Given one BUY item with no series and one BUY item with only 3 floats
    When I render the BUY sparklines
    Then the data URL is empty

  Scenario: BUY sparklines cap at 8 panels per email
    Given 12 BUY items each with 30-float close series
    When I render the BUY sparklines
    Then the data URL is a base64 PNG
