Feature: Signal ledger

  Append-only JSONL evidence log for every COMPASS / CATALYST signal.
  Tracks outcome so model performance can be measured objectively —
  no cherry-picking possible because every signal is written at fire time.

  All scenarios use a temp-file ledger to avoid touching the real
  ~/.tradepro/signal_ledger.jsonl.

  # ──────────────────────────────────────────────────────────────────
  # Append and load
  # ──────────────────────────────────────────────────────────────────

  Scenario: Append one signal and load it back
    Given a fresh temp-file ledger
    When a COMPASS signal is appended for "AAPL" with score 82.0 and entry_price 185.0
    Then load_all returns 1 record
    And the record has status "OPEN"
    And the record has source "COMPASS"
    And the record has symbol "AAPL"

  Scenario: Signal gets a unique UUID
    Given a fresh temp-file ledger
    When two COMPASS signals are appended for different symbols
    Then each record has a distinct signal_id

  Scenario: Appended signal has fired_at populated
    Given a fresh temp-file ledger
    When a COMPASS signal is appended for "MSFT" with score 70.0 and entry_price 420.0
    Then the record has a non-empty fired_at timestamp

  Scenario: Invalid source raises ValueError
    When SignalRecord.new is called with source "UNKNOWN"
    Then a ValueError is raised

  # ──────────────────────────────────────────────────────────────────
  # Close signal
  # ──────────────────────────────────────────────────────────────────

  Scenario: Close a signal with HIT_TARGET outcome
    Given a fresh temp-file ledger with one OPEN signal for "NVDA" entry_price=500.0
    When close_signal is called with outcome "HIT_TARGET" exit_price=550.0
    Then the signal has status "CLOSED"
    And outcome is "HIT_TARGET"
    And return_pct is approximately 10.0
    And holding_days is at least 0

  Scenario: Close a signal with STOPPED_OUT outcome
    Given a fresh temp-file ledger with one OPEN signal for "AMD" entry_price=180.0
    When close_signal is called with outcome "STOPPED_OUT" exit_price=162.0
    Then the signal has status "CLOSED"
    And outcome is "STOPPED_OUT"
    And return_pct is approximately -10.0

  Scenario: close_signal returns False for unknown signal_id
    Given a fresh temp-file ledger
    When close_signal is called for signal_id "nonexistent-uuid" with outcome "EXPIRED"
    Then the return value is False

  Scenario: Invalid outcome raises ValueError
    Given a fresh temp-file ledger with one OPEN signal for "GOOG" entry_price=170.0
    When close_signal is called with outcome "WRONG_OUTCOME"
    Then a ValueError is raised

  # ──────────────────────────────────────────────────────────────────
  # expire_stale
  # ──────────────────────────────────────────────────────────────────

  Scenario: expire_stale closes signals past expires_at
    Given a fresh temp-file ledger
    And a signal with expires_days=-1 (already expired)
    When expire_stale is called
    Then the signal has status "CLOSED"
    And outcome is "EXPIRED"
    And expire_stale returns 1

  Scenario: expire_stale does not close a fresh signal
    Given a fresh temp-file ledger with one OPEN signal with expires_days=5
    When expire_stale is called
    Then the signal still has status "OPEN"
    And expire_stale returns 0

  # ──────────────────────────────────────────────────────────────────
  # compute_stats
  # ──────────────────────────────────────────────────────────────────

  Scenario: compute_stats with no closed signals returns None metrics
    Given a fresh temp-file ledger
    When compute_stats is called
    Then total_closed is 0
    And hit_rate_pct is None
    And expectancy_pct is None

  Scenario: compute_stats with three wins and two losses
    Given a fresh temp-file ledger with closed signals
      | source  | outcome      | return_pct |
      | COMPASS | HIT_TARGET   | 3.0        |
      | COMPASS | HIT_TARGET   | 4.0        |
      | COMPASS | HIT_TARGET   | 2.5        |
      | COMPASS | STOPPED_OUT  | -1.5       |
      | COMPASS | STOPPED_OUT  | -2.0       |
    When compute_stats is called
    Then total_closed is 5
    And hit_rate_pct is 60.0
    And expectancy_pct is positive

  Scenario: compute_stats filtered by source
    Given a fresh temp-file ledger with signals from both COMPASS and CATALYST
    When compute_stats is called with source "COMPASS"
    Then only COMPASS signals appear in total_closed

  Scenario: compute_stats filtered by symbol
    Given a fresh temp-file ledger with signals for AAPL and MSFT
    When compute_stats is called with symbol "AAPL"
    Then only AAPL signals appear in total_closed

  Scenario: compute_stats with lookback_days filters recent signals
    Given a fresh temp-file ledger with signals closed 10 days ago and 60 days ago
    When compute_stats is called with lookback_days=30
    Then only the signal closed 10 days ago is counted

  # ──────────────────────────────────────────────────────────────────
  # load_open / load_closed
  # ──────────────────────────────────────────────────────────────────

  Scenario: load_open excludes CLOSED signals
    Given a fresh temp-file ledger with one OPEN and one CLOSED signal
    When load_open is called
    Then the result contains only the OPEN signal

  Scenario: load_closed excludes OPEN signals
    Given a fresh temp-file ledger with one OPEN and one CLOSED signal
    When load_closed is called
    Then the result contains only the CLOSED signal

  Scenario: implied_rr computed correctly
    Given a SignalRecord with entry_price=100.0 stop_price=95.0 target_price=115.0
    When implied_rr is accessed
    Then the value is 3.0
