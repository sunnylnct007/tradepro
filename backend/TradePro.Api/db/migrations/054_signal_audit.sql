-- 054_signal_audit.sql
--
-- Stores the "Signal vs Position" audit artifact emitted by `tradepro-signal-audit`
-- on the Mac worker. For each strategy it reads the BROKER-GOLDEN held positions
-- and re-computes the trader's stateful Ichimoku signal on each held name, then
-- flags the DIVERGENCES:
--   • exit-overdue  — signal says FLAT (exit) but the position is still held
--   • blind         — held but no bars in the cache → we can't evaluate its exit
-- This surfaces the "running blind / exits not firing" gap the UI otherwise hides
-- (a held loser whose exit signal fired days ago but never executed).
--
-- One row per (strategy, label); artifact is an opaque JSONB blob matching the
-- CLI emit shape. Same minimal pattern as today_setups_results / fill_replay_results.

CREATE TABLE IF NOT EXISTS signal_audit_results (
    strategy        TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT 'latest',
    artifact        JSONB NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    uploaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     TEXT,
    note            TEXT,
    PRIMARY KEY (strategy, label)
);

CREATE INDEX IF NOT EXISTS idx_signal_audit_results_as_of
    ON signal_audit_results(strategy, as_of_utc DESC);
