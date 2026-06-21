-- 049_options_candidate_screen.sql
--
-- Latest wheel candidate-screen snapshot for the Options Desk. The Mac-side
-- `tradepro-options-screen` job (IV-Rank + regime + risk engine) POSTs the whole
-- screen as one JSON payload; the Options tab GETs it. Single-row table (id=1) —
-- we only ever keep the most recent screen; history lives in the audit log later.
-- Same "Mac computes → API stores → frontend reads" pattern as bar_cache_health.

CREATE TABLE IF NOT EXISTS options_candidate_screen (
    id              SMALLINT PRIMARY KEY DEFAULT 1,
    payload         JSONB NOT NULL,
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT options_candidate_screen_single_row CHECK (id = 1)
);
