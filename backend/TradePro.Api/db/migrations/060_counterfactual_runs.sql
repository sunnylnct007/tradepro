-- 060_counterfactual_runs.sql
-- Evidence Store (TRADEPRO_REDESIGN_SPEC.md §7.0): "We need to store this
-- valuable run info and collect as much as we go to make our system mature."
-- A counterfactual_run is a scored replay — what would have happened if a
-- skipped signal had been taken, or how a symbol has historically behaved
-- after an event (the META post-crash event study). Two real seed rows exist
-- in ~/sourcecode/tradepro/counterfactuals.jsonl (skip_cohort_replay,
-- event_study) — genuinely different shapes (cohort-of-symbols vs one-symbol/
-- many-events), hence `results` as a flexible JSONB payload rather than a
-- rigid per-kind schema, same pattern as verdicts.spec/evidence.
-- Additive + idempotent so it can't disturb live data.

CREATE TABLE IF NOT EXISTS counterfactual_runs (
    id           BIGSERIAL PRIMARY KEY,
    user_id      INT NOT NULL DEFAULT 1,
    run_ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind         TEXT NOT NULL,
    -- Nullable: a skip-cohort replay scores many symbols at once, not one.
    symbol       TEXT,
    generated_by TEXT NOT NULL DEFAULT 'manual',
    cohort       JSONB,
    method       JSONB,
    results      JSONB NOT NULL,
    findings     JSONB,
    caveats      JSONB,
    spec_version TEXT
);

CREATE INDEX IF NOT EXISTS ix_counterfactual_runs_symbol_ts ON counterfactual_runs (symbol, run_ts DESC);
CREATE INDEX IF NOT EXISTS ix_counterfactual_runs_kind_ts ON counterfactual_runs (kind, run_ts DESC);
