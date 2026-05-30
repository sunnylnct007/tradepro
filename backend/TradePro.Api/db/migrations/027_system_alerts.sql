-- 027_system_alerts.sql
--
-- Operational alert feed surfaced in the cockpit (alert banner). Raised
-- by the Mac daemons and any future backend monitor. The first producer
-- is the paper-session fail-closed guard: when a strategy cannot confirm
-- its current position from the broker (the golden source), it ABORTS
-- the run with no orders and raises a 'position_seed_failed' alert so the
-- operator can see — at a glance — that a strategy has stopped trading,
-- rather than discovering it from a silent broker timeout in a log file.
--
-- Append-mostly with dedup: an OPEN alert sharing a dedup_key is
-- refreshed (occurrences++, last_seen bumped) instead of duplicated, so a
-- failure that repeats every 15 min shows as one banner, not fifty.
CREATE TABLE IF NOT EXISTS system_alerts (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT         NOT NULL,                 -- e.g. 'paper-session'
    severity        TEXT         NOT NULL DEFAULT 'warn'
                                 CHECK (severity IN ('info', 'warn', 'critical')),
    code            TEXT         NOT NULL DEFAULT '',       -- machine code, e.g. 'position_seed_failed'
    title           TEXT         NOT NULL,
    detail          TEXT         NOT NULL DEFAULT '',
    strategy_id     TEXT,
    broker          TEXT,
    symbols         JSONB        NOT NULL DEFAULT '[]'::jsonb,
    dedup_key       TEXT,
    occurrences     INT          NOT NULL DEFAULT 1,
    first_seen_utc  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    last_seen_utc   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at_utc TIMESTAMPTZ,
    resolved_by     TEXT
);

-- At most one OPEN alert per dedup_key — repeats refresh that single row.
-- Resolved rows drop out of the index so the same condition can re-open
-- later (history is preserved as separate resolved rows).
CREATE UNIQUE INDEX IF NOT EXISTS system_alerts_open_dedup
    ON system_alerts (dedup_key)
    WHERE resolved_at_utc IS NULL AND dedup_key IS NOT NULL;

-- Fast "what's active right now?" read for the cockpit banner.
CREATE INDEX IF NOT EXISTS system_alerts_open
    ON system_alerts (last_seen_utc DESC)
    WHERE resolved_at_utc IS NULL;

COMMENT ON TABLE system_alerts IS
    'Operational alerts surfaced in the cockpit. dedup_key collapses an open alert so repeats refresh rather than flood; resolved_at_utc clears the banner.';
