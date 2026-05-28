Feature: Trade Streaming
  As a downstream analytics consumer
  I want trade events to be streamed to Kinesis
  So that trading data is available for position calculation and Databricks analysis
  (Kinesis is connected to Firehose at the AWS level for long-term S3 storage)

  Background:
    Given Kinesis streaming is enabled

  Scenario: Trade event is sent to Kinesis trades stream
    Given a sell trade for contract "2025-01-15T12:00:00Z to 2025-01-15T12:30:00Z"
    When the trade event is received via SignalR
    Then the trade is sent to the Kinesis trades stream

  Scenario: Multiple trades are all streamed individually
    Given 3 trade events
    When the trade message is received via SignalR
    Then 3 records are sent to the Kinesis trades stream

  Scenario: Empty trades list is handled gracefully
    Given an empty trades message
    When the trade message is received via SignalR
    Then no trade records are sent to Kinesis
