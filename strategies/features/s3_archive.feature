Feature: push_to_api — optional S3 archive after a successful API push
  The archive is opt-in at two layers (env var + boto3 install) so the
  default worker behaviour never depends on AWS-specific deps. Pin the
  contract: gating works, key shape is stable, errors are non-fatal.

  Scenario: env var not set → archive is a silent no-op
    Given the TRADEPRO_S3_ARCHIVE env is unset
    When I call _maybe_archive_to_s3 with a compare payload
    Then no S3 upload is attempted

  Scenario: env var set but boto3 unavailable → graceful skip with warning
    Given the TRADEPRO_S3_ARCHIVE env is "1"
    And boto3 is not importable
    When I call _maybe_archive_to_s3 with a compare payload
    Then stderr mentions "boto3 not installed"

  Scenario: compare key includes universe and run_id
    Given a compare payload for universe "etf_us_core" with run_id "abc123"
    When I build the S3 archive key
    Then the key equals "compare/etf_us_core/abc123.json"

  Scenario: heartbeat key includes host and a UTC timestamp
    Given a heartbeat payload from host "mac-001"
    When I build the S3 archive key
    Then the key starts with "heartbeat/mac-001/"
    And the key ends with ".json"

  Scenario: missing run_id falls back to a UTC timestamp
    Given a compare payload for universe "etf_us_core" with no run_id
    When I build the S3 archive key
    Then the key starts with "compare/etf_us_core/"
    And the key ends with ".json"

  Scenario: slashes in universe / host are escaped so the key stays clean
    Given a compare payload for universe "weird/slash/u" with run_id "run42"
    When I build the S3 archive key
    Then the key equals "compare/weird_slash_u/run42.json"
