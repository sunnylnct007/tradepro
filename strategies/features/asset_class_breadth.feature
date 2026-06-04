Feature: Asset class breadth — us_equity / uk_equity / fx_spot / crypto
  Trustworthy data layer now covers every universe TradePro trades
  today: ETFs (us_etf), US single names (us_equity), LSE listings
  (uk_equity), spot FX (fx_spot), and crypto (crypto). Each plugin
  registers on import, exposes the right calendar + bar counts, and
  validates frames against its schema.

  Scenario: every shipped plugin is registered
    When I list the registered asset classes
    Then the registry contains "us_etf"
    And the registry contains "us_equity"
    And the registry contains "uk_equity"
    And the registry contains "fx_spot"
    And the registry contains "crypto"

  Scenario Outline: each plugin advertises the standard resolution ladder
    Given the asset class "<asset>"
    Then the supported resolutions are "1m, 5m, 15m, 30m, 1h, 1d"

    Examples:
      | asset      |
      | us_etf     |
      | us_equity  |
      | uk_equity  |
      | fx_spot    |
      | crypto     |

  Scenario: US-listed sessions skip weekends and NYSE holidays
    Given the asset class "us_equity"
    When I ask for expected sessions between 2026-05-22 and 2026-05-29
    Then the session list excludes 2026-05-23
    And the session list excludes 2026-05-24
    And the session list excludes 2026-05-25
    And the session list includes 2026-05-22
    And the session list includes 2026-05-26

  Scenario: LSE sessions exclude UK bank holidays
    Given the asset class "uk_equity"
    When I ask for expected sessions between 2026-05-01 and 2026-05-08
    Then the session list excludes 2026-05-02
    And the session list excludes 2026-05-03
    And the session list excludes 2026-05-04
    And the session list includes 2026-05-01
    And the session list includes 2026-05-05

  Scenario: LSE full-day 1m bar count is 510 (08:00–16:30)
    Given the asset class "uk_equity"
    Then the expected 1m bar count for 2026-05-06 is 510

  Scenario: FX session list keeps Sundays and skips only Saturdays
    Given the asset class "fx_spot"
    When I ask for expected sessions between 2026-05-22 and 2026-05-25
    Then the session list excludes 2026-05-23
    And the session list includes 2026-05-22
    And the session list includes 2026-05-24
    And the session list includes 2026-05-25

  Scenario: FX schema makes volume nullable
    Given the asset class "fx_spot"
    Then volume is in the nullable column set
    And the schema_version is "fx_v1"

  Scenario: Crypto sessions include every calendar day
    Given the asset class "crypto"
    When I ask for expected sessions between 2026-05-22 and 2026-05-26
    Then the session list has 5 entries

  Scenario: Crypto 1d bar count is 1
    Given the asset class "crypto"
    Then the expected 1d bar count for 2026-05-25 is 1

  Scenario: Crypto schema requires volume (unlike FX)
    Given the asset class "crypto"
    Then volume is NOT in the nullable column set
    And the schema_version is "crypto_v1"
