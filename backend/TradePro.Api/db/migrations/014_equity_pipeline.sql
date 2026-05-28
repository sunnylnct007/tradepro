-- 014_equity_pipeline.sql
--
-- Stores the latest equity-pipeline backtest artifact emitted by
-- `tradepro-equity-pipeline` (the trader's StrategyRunner port —
-- sleeves + ensemble + walk-forward + Monte Carlo + plot data).
--
-- Schema is intentionally minimal: one row per (strategy, label).
-- "label" lets the trader keep multiple labeled runs side-by-side
-- ("with-hibeta", "no-mc", "2020-2025") without each one wiping
-- the last. The 'latest' label is what the strategy page defaults
-- to reading.
--
-- artifact is a JSONB blob matching the CLI's emit shape
-- (in_sample / walk_forward / spy_benchmark / monte_carlo / charts).
-- Keeping it as a single blob means schema evolution on the CLI side
-- doesn't break the API — the UI is the only consumer of the inner
-- structure.

CREATE TABLE IF NOT EXISTS equity_pipeline_results (
    strategy        TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT 'latest',
    artifact        JSONB NOT NULL,
    as_of_utc       TIMESTAMPTZ NOT NULL,
    uploaded_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by     TEXT,
    note            TEXT,
    PRIMARY KEY (strategy, label)
);

CREATE INDEX IF NOT EXISTS idx_equity_pipeline_results_as_of
    ON equity_pipeline_results(strategy, as_of_utc DESC);
