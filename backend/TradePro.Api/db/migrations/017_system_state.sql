-- 017_system_state.sql
--
-- Kill switch + fail-safe defaults. One central row that every
-- order-dispatching code path checks before placing trades.
--
-- Three modes:
--   normal  — everything operates as designed.
--   frozen  — no NEW positions, no auto-rebalance. Existing positions
--             hold; defensive exits still work (so risk module can
--             cut losers); but the slow loop's BUY intents are
--             refused at the OMS gate. Operator's planned pause.
--   panic   — hard stop. Refuse EVERY new order. Cancel any pending.
--             For "something is very wrong, don't trust anything"
--             scenarios.
--
-- The pattern matches the risk module's defensive overrides — system_state
-- is the OPERATOR's manual switch; risk events are the AUTOMATED gates.
-- Both feed the same OMS pre-dispatch check.
--
-- Single-row table — enforced with a check constraint on a fixed PK so
-- INSERT can only succeed for the one known row. Updates change the
-- mode + reason. INSERT seeds the row on migration so reads never miss.

CREATE TABLE IF NOT EXISTS system_state (
    id            INT PRIMARY KEY CHECK (id = 1),
    mode          TEXT NOT NULL CHECK (mode IN ('normal', 'frozen', 'panic')),
    reason        TEXT,
    set_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    set_by        TEXT NOT NULL DEFAULT 'system'
);

INSERT INTO system_state (id, mode, reason, set_by)
VALUES (1, 'normal', 'initial state', 'migration')
ON CONFLICT (id) DO NOTHING;

-- Audit log — every mode change is recorded so we can answer "when
-- was the system frozen? who? why?" without losing history when
-- system_state is mutated. Append-only.
CREATE TABLE IF NOT EXISTS system_state_events (
    id             BIGSERIAL PRIMARY KEY,
    prior_mode     TEXT NOT NULL,
    new_mode       TEXT NOT NULL,
    reason         TEXT,
    changed_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    changed_by     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_state_events_recent
    ON system_state_events(changed_at_utc DESC);
