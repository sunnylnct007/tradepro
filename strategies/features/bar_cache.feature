Feature: Trustworthy bar cache (Phase B-1)
  The BarStore is the foundation of the Trustworthy data layer. Its
  promises:
    * Cached partitions hit the cache on the next read (no duplicate
      provider call).
    * Provider chain falls through on rate limit / error to the next
      provider.
    * Atomic Parquet writes — no partial files survive a crash mid-write.
    * Manifest violations refuse the read (silent partial reads are
      banned).
    * Partial coverage raises a structured BarFetchError by default;
      allow_partial opts in.
    * Telemetry event emits on every fetch (cache hit, miss, error).
    * Schema version mismatch refuses the read.

  These scenarios use synthetic providers + a tmpdir cache so no
  network calls and no real disk pollution.

  Background:
    Given a fresh tmp bar cache base directory
    And the us_etf asset class plugin is registered

  # ──────────────────────────────────────────────────────────────────
  # Section 1: Happy path — fetch, cache, re-read
  # ──────────────────────────────────────────────────────────────────

  Scenario: First fetch is a cache miss → provider call → atomic write
    Given a provider "yfinance" returning 390 bars for 2024-12-23
    When I get SPY us_etf 1m bars from 2024-12-23 to 2024-12-24 (allow_partial)
    Then the BarFrame has 390 rows
    And the chain shows a cache_miss followed by yfinance_ok
    And a parquet file exists for partition 2024-12
    And a manifest file exists for partition 2024-12
    And the manifest declares expected sessions including 2024-12-23

  Scenario: Second fetch with cached partition is a cache hit
    Given a provider "yfinance" returning a full December 2024 month
    When I get SPY us_etf 1m bars for full December 2024 (twice)
    Then the second call's chain is exactly cache_hit
    And the second call's provider_used is "cache"
    And the row counts match between the two calls

  # ──────────────────────────────────────────────────────────────────
  # Section 2: Failure modes — chain fallback, errors, partial coverage
  # ──────────────────────────────────────────────────────────────────

  Scenario: Partial coverage raises by default
    Given a provider "yfinance" returning bars only for 2024-12-23
    When I get SPY us_etf 1m bars for full December 2024 without allow_partial
    Then a BarFetchError is raised with error_class "partial_coverage"
    And the error's actual.missing_sessions includes "2024-12-26"

  Scenario: allow_partial returns the partial frame with coverage_complete False
    Given a provider "yfinance" returning bars only for 2024-12-23
    When I get SPY us_etf 1m bars for full December 2024 with allow_partial
    Then the BarFrame coverage_complete is False
    And the rows_returned is less than the rows_expected

  Scenario: Manifest violation when on-disk parquet contradicts the manifest
    Given a previously cached SPY partition 2024-12 with manifest claiming 8010 bars
    When I corrupt the manifest to claim a different schema version
    And I get SPY us_etf 1m bars for December 2024
    Then a SchemaVersionMismatch error is raised

  Scenario: Unsupported resolution fails loudly before any provider call
    When I get SPY us_etf bars at resolution "999s" from 2024-12-23 to 2024-12-31
    Then a BarFetchError is raised with error_class "schema"
    And no provider was called

  # ──────────────────────────────────────────────────────────────────
  # Section 3: Telemetry
  # ──────────────────────────────────────────────────────────────────

  Scenario: A successful cache hit emits one event with result "complete"
    Given a provider "yfinance" returning a full December 2024 month
    And a recording telemetry sink
    When I get SPY us_etf 1m bars for full December 2024 (twice)
    Then the recording sink received at least 2 events
    And the most recent event has result "complete"
    And the most recent event source_chain is exactly ["cache_hit"]

  Scenario: A partial fetch emits one event with result "fetched_partial"
    Given a provider "yfinance" returning bars only for 2024-12-23
    And a recording telemetry sink
    When I get SPY us_etf 1m bars for full December 2024 with allow_partial
    Then the recording sink received at least 1 event
    And the most recent event has result "fetched_partial"
    And the most recent event gaps_detected_count is greater than 0

  # ──────────────────────────────────────────────────────────────────
  # Section 4: Atomic writes
  # ──────────────────────────────────────────────────────────────────

  Scenario: Atomic write — no tmp file remains after a successful write
    Given a provider "yfinance" returning a full December 2024 month
    When I get SPY us_etf 1m bars for full December 2024
    Then no .tmp file exists under the cache directory
    And the parquet and manifest files exist for partition 2024-12

  # ──────────────────────────────────────────────────────────────────
  # Section 5: BackendTelemetrySink (Phase B-2)
  # ──────────────────────────────────────────────────────────────────
  # BackendTelemetrySink POSTs every event to the backend endpoint
  # AND appends to the local JSONL. Both are best-effort — a fetch
  # never fails because telemetry failed.

  Scenario: BackendTelemetrySink POSTs an event on each fetch
    Given a provider "yfinance" returning a full December 2024 month
    And a BackendTelemetrySink with a recording HTTP poster
    When I get SPY us_etf 1m bars for full December 2024 via the backend sink
    Then the HTTP poster received at least 1 request
    And the POST URL ends with "/api/admin/data-trust/bar-cache/events"
    And the POST body's canonical is "SPY"
    And the JSONL fallback file exists

  Scenario: BackendTelemetrySink swallows a failed POST and keeps fetching
    Given a provider "yfinance" returning a full December 2024 month
    And a BackendTelemetrySink whose HTTP poster raises an exception
    When I get SPY us_etf 1m bars for full December 2024 via the backend sink
    Then the BarFrame coverage_complete is True
    And the JSONL fallback file exists

  # ──────────────────────────────────────────────────────────────────
  # Section 6: DB-driven provider chain (Phase B-3)
  # ──────────────────────────────────────────────────────────────────
  # PreferencesLoader pulls the provider chain from the backend's
  # /api/admin/data-trust/preferences endpoint per (asset_class,
  # resolution). BarStore uses it when configured; falls back to the
  # hardcoded default chain when the loader has no opinion or fails.

  Scenario: PreferencesLoader chain takes precedence over the default
    Given a PreferencesLoader returning ["yfinance"] for us_etf 1m
    And a provider "yfinance" returning a full December 2024 month
    When I get SPY us_etf 1m bars for full December 2024 via the BarStore with that loader
    Then the chain_source breadcrumb in the source_chain is "preferences"
    And the manifest's provider_chain is ["yfinance"]

  Scenario: Loader miss falls back to the BarStore default chain
    Given a PreferencesLoader returning no preference for us_etf 1m
    And a provider "yfinance" returning a full December 2024 month
    When I get SPY us_etf 1m bars for full December 2024 via the BarStore with that loader
    Then the chain_source breadcrumb in the source_chain is "default"

  Scenario: Loader HTTP failure falls back to the default chain
    Given a PreferencesLoader whose HTTP getter raises an exception
    And a provider "yfinance" returning a full December 2024 month
    When I get SPY us_etf 1m bars for full December 2024 via the BarStore with that loader
    Then the chain_source breadcrumb in the source_chain is "default"
    And the BarFrame coverage_complete is True

  Scenario: PreferencesLoader caches snapshots within its TTL
    Given a PreferencesLoader with a recording HTTP getter and 60s TTL
    When I call chain_for us_etf 1m twice in a row
    Then the HTTP getter received exactly 1 request

  Scenario: PreferencesLoader clear_cache forces a re-fetch
    Given a PreferencesLoader with a recording HTTP getter and 60s TTL
    When I call chain_for us_etf 1m
    And I clear the PreferencesLoader cache
    And I call chain_for us_etf 1m
    Then the HTTP getter received exactly 2 requests
