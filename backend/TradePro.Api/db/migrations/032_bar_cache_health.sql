-- 032_bar_cache_health.sql
--
-- Per-symbol health record, updated incrementally on every
-- BarStore.get() call. The cockpit reads this table for the
-- per-symbol "is the data layer healthy for X?" answer.
--
-- This is derived state — it could in principle be computed from
-- the bar_cache_events log. Persisting it as a table means the
-- UI panel doesn't need a heavyweight aggregation query on every
-- render, and means an investigator can see "last successful
-- fetch was 18 hours ago" without scanning the event log.
--
-- The Python side writes via UPSERT on (canonical, asset_class).
-- Phase A added the migration runner; this migration adds the
-- table the Phase B cache populates. Phase G adds the UI panel
-- that consumes it.

CREATE TABLE IF NOT EXISTS bar_cache_health (
    canonical              TEXT NOT NULL,
    asset_class            TEXT NOT NULL,

    -- Last-touch state
    last_fetched_at_utc    TIMESTAMPTZ,
    last_fetched_result    TEXT,
    last_fetched_provider  TEXT,
    last_fetched_resolution TEXT,

    -- Coverage envelope — what we have on disk
    coverage_start_date    DATE,
    coverage_end_date      DATE,
    coverage_partitions    INT NOT NULL DEFAULT 0,
    missing_days_count     INT NOT NULL DEFAULT 0,

    -- Integrity tracking
    schema_version         TEXT,
    manifest_violations_last_30d INT NOT NULL DEFAULT 0,

    -- Lifecycle events that matter to a future investigator
    last_corp_action_at_utc TIMESTAMPTZ,
    last_corp_action_type   TEXT,

    -- Bookkeeping
    updated_at_utc         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (canonical, asset_class)
);

CREATE INDEX IF NOT EXISTS bar_cache_health_updated
    ON bar_cache_health (updated_at_utc DESC);

CREATE INDEX IF NOT EXISTS bar_cache_health_missing_days
    ON bar_cache_health (missing_days_count DESC, updated_at_utc DESC);

COMMENT ON TABLE bar_cache_health IS
    'Per-symbol cache health snapshot. Phase B writes; Phase G UI panel reads.';
