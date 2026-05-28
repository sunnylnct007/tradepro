Feature: OrderBook Persistence
  As a data consumer
  I want orderbook snapshots to be persisted to DynamoDB and S3
  So that I can query historical market depth for backtesting and analytics

  Background:
    Given all AWS sinks are enabled

  Scenario: Orderbook snapshot is persisted to DynamoDB and S3
    Given a valid orderbook snapshot for market "Intraday" and delivery area "NGET"
    When the orderbook message is received via SignalR
    Then the snapshot is saved to DynamoDB with the correct partition key "Intraday"
    And the snapshot is saved to S3 with a hive-style path

  Scenario: Multiple orderbook snapshots are all persisted independently
    Given 3 orderbook snapshots for different delivery windows
    When the orderbook message is received via SignalR
    Then 3 items are saved to DynamoDB
    And 3 objects are saved to S3

  Scenario: DynamoDB failure does not block S3
    Given a valid orderbook snapshot for market "DayAhead" and delivery area "NGET"
    And DynamoDB is unavailable
    When the orderbook message is received via SignalR
    Then the snapshot is still saved to S3

  Scenario: Empty orderbook list is handled gracefully
    Given an empty orderbook message
    When the orderbook message is received via SignalR
    Then no items are saved to any sink

  Scenario Outline: DynamoDB sort key format is correct for various delivery windows
    Given a contract with delivery start "<start>" and delivery end "<end>"
    When the sort key is generated
    Then the sort key is "<start>#<end>"

    Examples:
      | start                    | end                      |
      | 2025-01-15T12:00:00.0000000Z | 2025-01-15T12:30:00.0000000Z |
      | 2025-06-01T08:00:00.0000000Z | 2025-06-01T09:00:00.0000000Z |
