Feature: SignalR Disconnect Backfill
  As a system operator
  I want missed orders and trades to be backfilled from the REST API on reconnection
  So that no events are lost during a SignalR disconnect

  Scenario: Orders are backfilled after reconnection
    Given the SignalR connection was last active at "2025-01-15T12:00:00Z"
    And the connection was re-established at "2025-01-15T12:05:00Z"
    When the backfill runs for the missed window
    Then the REST API is called for orders between "2025-01-15T12:00:00Z" and "2025-01-15T12:05:00Z"
    And the backfilled orders are sent to Kinesis

  Scenario: Trades are backfilled after reconnection
    Given the SignalR connection was last active at "2025-01-15T12:00:00Z"
    And the connection was re-established at "2025-01-15T12:05:00Z"
    When the backfill runs for the missed window
    Then the REST API is called for trades between "2025-01-15T12:00:00Z" and "2025-01-15T12:05:00Z"
    And the backfilled trades are sent to Kinesis

  Scenario: Backfill is skipped when disconnect duration is too long
    Given the SignalR connection was last active at "2025-01-15T10:00:00Z"
    And the connection was re-established at "2025-01-15T14:00:00Z"
    When the backfill runs for the missed window
    Then the backfill is skipped and a warning is logged
