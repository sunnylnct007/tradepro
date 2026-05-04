Feature: run_comparison can push results back to the API cache
  When a Claude Desktop user asks for a fresh comparator run, the
  result should optionally land in the same file cache the UI reads
  — so a "refresh QQQ" question via chat actually updates what the
  browser shows next time. The push uses the same credentials path
  as `tradepro-push`; never blocks the comparison run if creds are
  missing.

  Scenario: credentials loader prefers the file when present
    Given a credentials file with base "https://prod.example/" and token "FILE_TOKEN"
    When I load push credentials
    Then the loaded base is "https://prod.example"
    And the loaded token is "FILE_TOKEN"
    And the loaded source is "file"

  Scenario: credentials loader falls back to env vars
    Given no credentials file
    And the env TRADEPRO_API_URL is "http://localhost:5080/" and TRADEPRO_API_TOKEN is "ENV_TOKEN"
    When I load push credentials
    Then the loaded base is "http://localhost:5080"
    And the loaded token is "ENV_TOKEN"
    And the loaded source is "env"

  Scenario: credentials loader returns None when nothing configured
    Given no credentials file
    And the env TRADEPRO_API_URL is unset and TRADEPRO_API_TOKEN is unset
    When I load push credentials
    Then the loaded base is None
    And the loaded token is None
    And the loaded source is "none"

  Scenario: push helper skips cleanly without credentials (does not crash)
    Given no credentials file
    And the env TRADEPRO_API_URL is unset and TRADEPRO_API_TOKEN is unset
    When I push a synthetic compare payload
    Then the push result is skipped with a clear reason
    And the push result is not ok
