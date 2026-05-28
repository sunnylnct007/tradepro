Feature: Document upload pipeline (Phase 5c-iii)
  Research papers, prospectuses, analyst notes are extracted on the
  Mac and pushed to the API as structured manifests. Behave coverage
  ensures the extractor + manifest contract holds without hitting
  Yahoo or Ollama.

  Scenario: Plain text file is extracted with sha256 + char_count
    Given a temp file containing "Sample analyst note on QQQ — Sharpe 0.94"
    When I run extract on that file
    Then the file_kind is text
    And the extracted char_count is greater than 0
    And the sha256 is a 64-character hex string

  Scenario: Manifest builds with linked symbols upper-cased
    Given a temp file containing "Foo bar"
    When I run extract on that file
    And I build a manifest with title "Test" and symbols "qqq, voo, "
    Then the manifest's linked_symbols are ["QQQ", "VOO"]
    And the manifest has a uuid doc_id
    And the manifest preserves the file's char_count

  Scenario: Unsupported extension is rejected
    Given a temp file with extension ".docx"
    When I run extract on that file
    Then a ValueError is raised mentioning supported types
