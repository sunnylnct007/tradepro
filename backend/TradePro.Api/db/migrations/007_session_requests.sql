-- ── session_requests ────────────────────────────────────────────────
-- Trigger queue for ops the user wants the Mac engine to run. Used
-- by Task #69 (intraday automation) and future UI-driven ops where
-- the API can't execute the work itself (data lives on the Mac, T212
-- demo key is on the Mac, etc.).
--
-- Flow:
--   1. UI POSTs /api/ops/run-intraday → row inserted as 'Pending'
--   2. Mac worker polls /api/ops/poll-intraday → atomic
--      UPDATE-RETURNING flips one Pending row to 'Claimed'
--   3. Mac runs the requested op, then POSTs
--      /api/ops/complete-intraday/{id} with the result summary →
--      row moves to 'Completed' (or 'Failed' on error)
--
-- State machine identical in spirit to pending_orders:
--   Pending → Claimed → Completed | Failed
--   Pending → Cancelled (user can cancel before pickup)
--
-- `kind` is keyed so future ops (run-compare, run-backtest, etc.)
-- can share the table — the queue is generic; the request payload
-- in `params` is what's op-specific.
CREATE TABLE IF NOT EXISTS session_requests (
    request_id        TEXT        PRIMARY KEY,
    kind              TEXT        NOT NULL,
    params            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    state             TEXT        NOT NULL DEFAULT 'Pending'
                      CHECK (state IN ('Pending', 'Claimed', 'Completed', 'Failed', 'Cancelled')),
    requested_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_at_utc    TIMESTAMPTZ,
    claimed_by        TEXT,
    completed_at_utc  TIMESTAMPTZ,
    result_summary    JSONB,
    error             TEXT
);

CREATE INDEX IF NOT EXISTS session_requests_state_requested
    ON session_requests (state, requested_at_utc ASC);

CREATE INDEX IF NOT EXISTS session_requests_kind_requested
    ON session_requests (kind, requested_at_utc DESC);

COMMENT ON TABLE session_requests IS
    'UI-triggered ops queue picked up by the Mac worker. State Pending -> Claimed -> Completed/Failed. See Task #69.';
COMMENT ON COLUMN session_requests.kind IS
    'Op identifier, e.g. "intraday", "compare". Mac filters by kind when polling.';
COMMENT ON COLUMN session_requests.params IS
    'Op-specific JSON payload — symbols, window, gate thresholds for intraday; date for compare; etc.';
