-- 034_paper_backtests_data_state_hash.sql
--
-- Phase D-2 of the trustworthy-data roadmap. The Phase D-1 BarStore
-- now stamps every BarFrame with a data_state_hash — a SHA256 digest
-- derived deterministically from the manifests of the partitions
-- read. This migration plumbs that hash into the backtest reports
-- table so a future cockpit view can answer "did this run use the
-- same data as the 2024-08 baseline?".
--
-- Nullable on purpose:
--   * Existing rows have no hash and we don't backfill them — they
--     were produced before D-1 landed, so we genuinely don't know
--     what data they ran on.
--   * Reports pushed from non-BarStore code paths (e.g. legacy
--     paper_session callers that still read via cache.py) won't have
--     a hash to stamp. The UI shows "no hash recorded" for those
--     rather than fabricating one.
--
-- The functional index on data_state_hash makes "find every report
-- that used this data" a single index scan — the lookup the result
-- viewer issues when a user clicks the hash chip. Partial index
-- skips NULLs so it stays small.

ALTER TABLE paper_backtests
    ADD COLUMN IF NOT EXISTS data_state_hash TEXT;

CREATE INDEX IF NOT EXISTS paper_backtests_data_state_hash
    ON paper_backtests (data_state_hash)
    WHERE data_state_hash IS NOT NULL;

COMMENT ON COLUMN paper_backtests.data_state_hash IS
    'Deterministic SHA256 hex digest of the BarStore partition '
    'fingerprints the report consumed. Two reports with the same '
    'hash MUST have used byte-identical bars. Phase D-1 produces it, '
    'Phase D-2 (this migration) stores it, Phase D-3 carries it '
    'through walk-forward / Monte Carlo per-leg.';
