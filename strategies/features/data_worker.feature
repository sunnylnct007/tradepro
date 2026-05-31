Feature: tradepro-data-worker — Phase C-Validate
  The Mac-side worker daemon polls the trustworthy-data ops queue
  and dispatches claimed sessions to handlers under
  ``tradepro_strategies.data_ops``. Phase C-Validate ships the
  ``data_validate`` handler only — non-destructive walk of the bar
  cache producing a per-resolution gap report. Subsequent slices
  add data_backfill, data_reload, data_repartition, data_purge
  handlers — each lands as a new file under
  ``data_ops/handlers/`` with no CLI change required.

  These scenarios exercise the handler logic against a local
  ``BarCacheStorage`` rooted at a tmpdir. The polling HTTP loop is
  not exercised (it mirrors the intraday + paper poll/complete
  pattern already covered in paper_quant_strategies.feature).

  Scenario: data_validate dispatch reports incomplete partitions
    Given a tmp bar cache populated with a single-day SPY partition
    When I dispatch a data_validate request for SPY us_etf
    Then the data op result is ok
    And the data op result detail canonical is "SPY"
    And the data op result detail asset_class is "us_etf"
    And the data op result detail exists is True
    And the data op result detail resolutions include "1m"
    And the 1m resolution incomplete_count is greater than 0

  Scenario: data_validate dispatch reports a complete partition
    Given a tmp bar cache populated with a full December 2024 SPY partition
    When I dispatch a data_validate request for SPY us_etf
    Then the data op result is ok
    And the 1m resolution complete_count is 1
    And the 1m resolution incomplete_count is 0

  Scenario: data_validate dispatch surfaces missing-symbol gracefully
    Given a tmp bar cache with no SPY directory
    When I dispatch a data_validate request for SPY us_etf
    Then the data op result is ok
    And the data op result detail exists is False
    And the data op result summary mentions "no cache directory"

  Scenario: data_validate dispatch rejects empty params
    When I dispatch a data_validate request with empty params
    Then the data op result is not ok
    And the data op result error mentions "missing required params"

  Scenario: dispatch with unregistered kind returns a structured error
    When I dispatch a data_op of kind "data_bogus_op"
    Then the data op result is not ok
    And the data op result summary mentions "no handler"

  Scenario: registry lists every registered kind
    When I list registered data_op kinds
    Then the registered kinds include "data_validate"

  Scenario: LocalBarCacheStorage.describe reports backend metadata
    Given a tmp bar cache with no SPY directory
    Then the storage describe reports backend "local"

  # ──────────────────────────────────────────────────────────────────
  # Phase C-Backfill — operator-triggered cache population
  # ──────────────────────────────────────────────────────────────────

  Scenario: data_backfill populates an empty cache via the provider chain
    Given a synthetic yfinance provider returning a full December 2024 month
    And a tmp bar cache with no SPY directory
    When I dispatch a data_backfill request for SPY us_etf 1m 2024-12-02 to 2024-12-31
    Then the data op result is ok
    And the data op result summary contains "via yfinance"
    And the data op result detail partitions_before is 0
    And the data op result detail partitions_after is 1
    And the data op result detail partitions_added is 1

  Scenario: data_backfill rejects missing required params
    When I dispatch a data_backfill request with empty params
    Then the data op result is not ok
    And the data op result error mentions "missing required params"
    And the data op result detail missing includes "canonical"
    And the data op result detail missing includes "asset_class"
    And the data op result detail missing includes "resolution"
    And the data op result detail missing includes "from"

  Scenario: data_backfill rejects unparseable from-date
    When I dispatch a data_backfill request for SPY us_etf 1m with from "not-a-date"
    Then the data op result is not ok
    And the data op result summary contains "date parse error"

  Scenario: data_backfill rejects when to is before from
    When I dispatch a data_backfill request for SPY us_etf 1m 2024-12-31 to 2024-12-02
    Then the data op result is not ok
    And the data op result summary contains "to date must be on or after from"

  Scenario: BackfillHandler is registered in the data_ops registry
    When I list registered data_op kinds
    Then the registered kinds include "data_backfill"
