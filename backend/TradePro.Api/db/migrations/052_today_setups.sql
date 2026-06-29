-- 052_today_setups.sql
--
-- Stores the latest "Today's Setups" artifact emitted by `tradepro-today-setups`
-- (screens a universe → per-symbol Ichimoku signal + range/risk → ranked by
-- ENTRY QUALITY: ⭐ consider / ⚠ extended / excluded). Powers the dashboard
-- scanner card — the curated, risk-aware replacement for the original flat
-- BUY-light dashboard.
--
-- One row per (universe, label); artifact is an opaque JSONB blob matching the
-- CLI emit shape (counts + ranked setups + excluded + missing). Same minimal
-- pattern as equity_pipeline_results / fill_replay_results.

CREATE TABLE IF NOT EXISTS today_setups_results (
    universe        TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT 'latest',
    artifact        JSONB NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    uploaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     TEXT,
    note            TEXT,
    PRIMARY KEY (universe, label)
);

CREATE INDEX IF NOT EXISTS idx_today_setups_results_as_of
    ON today_setups_results(universe, as_of_utc DESC);
