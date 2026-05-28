-- 015_strategy_decisions.sql
--
-- Per-(run, sleeve, symbol) decision log for the slow-loop / live
-- algo. Produced once per algo cycle (typically post-close or
-- pre-open) by `tradepro-live-portfolio`. Two reads off the same
-- table:
--
--   • Latest target portfolio — UI + MCP + risk module read the
--     most-recent run via (strategy → MAX(as_of_utc)). Today-only
--     by default per the no-clutter principle.
--   • Historical audit trail — when a live trade goes wrong in
--     N months we need to reconstruct what the algo saw + why it
--     recommended. Filter by `as_of_utc BETWEEN x AND y`.
--
-- Schema choices:
--   • run_id groups every (sleeve, symbol) row from one cycle so
--     "show me what the algo emitted at 16:05 UTC on 2026-05-27"
--     is a single index hit.
--   • target_weight + signal + regime_pass + vol kept as typed
--     columns (filterable / aggregable) — everything else lives
--     in JSONB `detail` for per-symbol reasons / indicator
--     values / multi-indicator vetoes etc.
--   • risk_class is a denormalised summary of the per-trade risk
--     classification we discussed (LOW/MEDIUM/HIGH/EXTREME). Kept
--     in a column so the trade-plan UI can render it without
--     unpacking JSONB.

-- DROP IF EXISTS first to recover from earlier half-formed schemas.
-- An earlier deploy created the table without the `strategy` column
-- (or some other partial state). Each restart since has crash-looped
-- on the CREATE INDEX referencing a column that doesn't exist. The
-- ALTER TABLE defensive pattern didn't help because PostgreSQL plans
-- the multi-statement migration body and sees the missing column on
-- the CREATE INDEX before ALTER TABLE executes.
--
-- Demo data only; safe to drop. CASCADE on the (then-uncreated)
-- index dependencies.
DROP INDEX IF EXISTS idx_strategy_decisions_latest;
DROP INDEX IF EXISTS idx_strategy_decisions_run;
DROP INDEX IF EXISTS idx_strategy_runs_latest;
DROP TABLE IF EXISTS strategy_decisions CASCADE;
DROP TABLE IF EXISTS strategy_runs CASCADE;

CREATE TABLE strategy_decisions (
    run_id            UUID NOT NULL,
    strategy          TEXT NOT NULL,
    sleeve            TEXT NOT NULL,
    symbol            TEXT NOT NULL,
    target_weight     DOUBLE PRECISION NOT NULL,
    signal            DOUBLE PRECISION NOT NULL,
    regime_pass       BOOLEAN NOT NULL DEFAULT TRUE,
    vol               DOUBLE PRECISION,
    risk_class        TEXT,                  -- LOW / MEDIUM / HIGH / EXTREME (null until risk module wires)
    detail            JSONB,                 -- reasons / indicator values / per-symbol context
    as_of_utc         TIMESTAMPTZ NOT NULL,
    uploaded_at_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by       TEXT,
    PRIMARY KEY (run_id, sleeve, symbol)
);

CREATE INDEX IF NOT EXISTS idx_strategy_decisions_latest
    ON strategy_decisions(strategy, as_of_utc DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_decisions_run
    ON strategy_decisions(run_id);

-- Header table — one row per run. Carries the run-level summary
-- (sleeve counts, regime state, ensemble stats, links to the
-- equity_pipeline_results row when the run was paired with a fresh
-- validation). Lets the UI render "last run: 16:05 UTC, 73 decisions,
-- regime=bull" without scanning the decisions table.
CREATE TABLE strategy_runs (
    run_id            UUID PRIMARY KEY,
    strategy          TEXT NOT NULL,
    mode              TEXT NOT NULL,         -- 'live' / 'backtest' / 'dry'
    as_of_utc         TIMESTAMPTZ NOT NULL,
    n_decisions       INT NOT NULL DEFAULT 0,
    n_long            INT NOT NULL DEFAULT 0,
    regime_state      TEXT,                  -- 'bull' / 'bear' / 'neutral'
    summary           JSONB,                 -- sleeves_meta, ensemble stats, inputs hash
    uploaded_at_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    uploaded_by       TEXT
);

CREATE INDEX idx_strategy_runs_latest
    ON strategy_runs(strategy, as_of_utc DESC);
