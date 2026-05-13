Feature: MCP get_compare — top_n + strip_bloat controls fit Claude's tool-result limit
  Without truncation a 65-row universe blows past the MCP size cap and
  Claude can't read the response at all. The tools.get_compare wrapper
  adds two knobs (top_n + strip_bloat / fields) so the default MCP
  invocation lands compact, with full payloads still available when
  explicitly requested.

  Scenario: top_n truncates the rows list and flags the result as truncated
    Given a fake compare API response with 30 rows
    When I call tools.get_compare with top_n 5
    Then the returned envelope has 5 rows
    And the returned response has truncated=true
    And the returned response has row_count_total=30

  Scenario: strip_bloat drops verbose fields per row but keeps identity + headline stats
    Given a fake compare API response with 3 rows carrying decision_trace and news
    When I call tools.get_compare with strip_bloat true
    Then no row carries decision_trace
    And no row carries news
    And every row still carries symbol
    And every row still carries _source

  Scenario: fields whitelist keeps only the named columns plus identity
    Given a fake compare API response with 3 rows carrying decision_trace and news
    When I call tools.get_compare with fields "bucket,stats"
    Then every row has exactly the keys symbol, strategy, _source, _source_symbol_best, bucket, stats
