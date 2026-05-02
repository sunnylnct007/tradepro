Feature: Order Streaming
  As a downstream OMS consumer
  I want order updates to be streamed to Kinesis
  So that the OMS can react to order state changes in real-time
  (Kinesis is connected to Firehose at the AWS level for long-term S3 storage)

  Background:
    Given Kinesis streaming is enabled

  Scenario: Order update is sent to Kinesis orders stream
    Given a buy order for contract "2025-01-15T12:00:00Z to 2025-01-15T12:30:00Z"
    When the order update is received via SignalR
    Then the order is sent to the Kinesis orders stream

  Scenario: Multiple orders are all sent to Kinesis
    Given 5 order updates
    When the order message is received via SignalR
    Then 5 records are sent to the Kinesis orders stream

  Scenario: Empty order list is handled gracefully
    Given an empty orders message
    When the order message is received via SignalR
    Then no order records are sent to Kinesis
