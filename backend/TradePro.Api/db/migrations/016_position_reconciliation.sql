-- 016_position_reconciliation.sql
--
-- Broker-as-golden position model with drift detection.
--
-- Architectural shift (per the systematic-trading discussion):
--   - Position state is read from the BROKER (T212 today, IBKR later)
--     and that is the source of truth.
--   - Our oms_orders + oms_fills tables remain the audit trail of
--     what THIS SYSTEM did — not a claim about what we hold.
--   - A reconciler runs every cycle and diffs "what the broker says we
--     hold" vs "what our fills imply we should hold." Differences are
--     logged here as drift events and surfaced in the UI banner +
--     daily email digest (per the alert preference).
--
-- We never auto-correct silently. A drift means EITHER our records
-- are stale (dividends, splits, manual trades in the broker app,
-- fractional weirdness) OR something genuinely went wrong with order
-- routing — humans need to look and decide. The reconciler's only
-- automated action is "block new orders on this symbol if drift is
-- critical," nothing else.
--
-- Severity tiers:
--   minor    — qty drift > 0 but < 1% of expected position size
--              (typically dividend reinvestment, fractional share
--              rounding). Logged for audit; no action.
--   major    — qty drift > 1% OR avg-price drift > 1%. Shown in the
--              in-app banner. Operator should review.
--   critical — qty drift > 5% OR a symbol we hold but our records
--              don't know about (or vice versa). Banner + email.
--              Eventually: block new orders on that symbol until
--              manually resolved.

CREATE TABLE IF NOT EXISTS position_drift_events (
    id                  BIGSERIAL PRIMARY KEY,
    broker              TEXT NOT NULL,              -- 'T212_DEMO' | 'T212_LIVE' | 'IBKR_PAPER' | 'IBKR_LIVE'
    symbol              TEXT NOT NULL,

    -- Snapshot at the moment of detection.
    broker_qty          NUMERIC,                    -- what the broker says we hold
    internal_qty        NUMERIC,                    -- what sum(fills) says we should hold
    qty_drift           NUMERIC NOT NULL,           -- broker - internal (signed)
    broker_avg_price    NUMERIC,
    internal_avg_price  NUMERIC,
    price_drift_pct     NUMERIC,                    -- (broker - internal) / internal × 100

    severity            TEXT NOT NULL CHECK (severity IN ('minor', 'major', 'critical')),
    detected_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Resolution: humans-only.
    resolved_at_utc     TIMESTAMPTZ,
    resolved_by         TEXT,
    resolution_note     TEXT
);

-- "Show me unresolved drift right now" — used by the banner. Partial
-- index so the open-drift queries are O(unresolved-count) regardless
-- of audit history size.
CREATE INDEX IF NOT EXISTS idx_position_drift_unresolved
    ON position_drift_events(broker, symbol)
    WHERE resolved_at_utc IS NULL;

CREATE INDEX IF NOT EXISTS idx_position_drift_recent
    ON position_drift_events(detected_at_utc DESC);
