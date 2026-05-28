-- ── OMS — Order Management System ──────────────────────────────────
-- Per ROADMAP §72 (OMS — proper persistence + lifecycle). Three
-- tables back every order the platform ever places — manual, paper,
-- algo — with full state-machine history, broker linkage, and
-- fill-by-fill reconciliation.
--
-- Phase 1 (this migration): tables + indexes. Service layer +
-- /api/oms/* endpoints + daemon wiring land in subsequent commits.
-- The "decisions" cascade table sketched in §72.4 is Phase 2.
--
-- Trimmed from the spec for Phase 1:
--   - parent_order_id (bracket/OCO) — wait until brackets exist
--   - signal_id / decision_id FKs — those tables aren't a hard dep yet
--   - time_in_force (kept as text default 'DAY' — broker-specific
--     enum can come later when IBKR plugs in)
-- All additive: future migrations add columns + FKs without breaking
-- the v1 contract.

-- ── oms_orders ────────────────────────────────────────────────────
-- Canonical lifecycle row per order. State changes append to
-- oms_order_events; this row carries the LATEST snapshot.
CREATE TABLE IF NOT EXISTS oms_orders (
    id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    client_order_id             UUID        NOT NULL UNIQUE,  -- our idempotency key
    broker                      TEXT        NOT NULL
                                CHECK (broker IN ('T212_DEMO', 'T212_LIVE', 'IBKR_PAPER', 'IBKR_LIVE', 'PAPER')),
    broker_order_id             TEXT,                          -- populated post-ACK
    strategy_id                 TEXT,                          -- nullable for manual orders
    symbol                      TEXT        NOT NULL,
    side                        TEXT        NOT NULL CHECK (side IN ('BUY', 'SELL')),
    qty                         NUMERIC     NOT NULL CHECK (qty > 0),
    order_type                  TEXT        NOT NULL
                                CHECK (order_type IN ('MKT', 'LMT', 'STP', 'STP_LMT')),
    limit_price                 NUMERIC,
    stop_price                  NUMERIC,
    time_in_force               TEXT        NOT NULL DEFAULT 'DAY',
    state                       TEXT        NOT NULL DEFAULT 'PENDING_APPROVAL'
                                CHECK (state IN (
                                    'PENDING_APPROVAL',
                                    'SUBMITTED',
                                    'WORKING',
                                    'PARTIALLY_FILLED',
                                    'FILLED',
                                    'CANCELLED',
                                    'REJECTED',
                                    'EXPIRED'
                                )),
    placed_by                   TEXT        NOT NULL DEFAULT 'STRATEGY_AUTO'
                                CHECK (placed_by IN ('HUMAN', 'STRATEGY_AUTO')),
    filled_qty                  NUMERIC     NOT NULL DEFAULT 0,
    avg_fill_price              NUMERIC,
    cancelled_reason            TEXT,
    created_at_utc              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_state_change_at_utc    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot queries for the OMS UI: "show me everything not terminal yet"
-- and "show me everything this strategy did". Partial index on the
-- open states keeps the working-orders scan tight even as terminal
-- rows accumulate.
CREATE INDEX IF NOT EXISTS oms_orders_open_state_idx
    ON oms_orders (state)
    WHERE state IN ('PENDING_APPROVAL', 'SUBMITTED', 'WORKING', 'PARTIALLY_FILLED');

CREATE INDEX IF NOT EXISTS oms_orders_strategy_id_idx
    ON oms_orders (strategy_id)
    WHERE strategy_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS oms_orders_created_at_idx
    ON oms_orders (created_at_utc DESC);


-- ── oms_order_events ──────────────────────────────────────────────
-- Append-only state-machine log. Every state change writes one row.
-- Reconstruct any order's full history with:
--   SELECT * FROM oms_order_events WHERE order_id = ? ORDER BY occurred_at_utc;
CREATE TABLE IF NOT EXISTS oms_order_events (
    id                  BIGSERIAL   PRIMARY KEY,
    order_id            UUID        NOT NULL REFERENCES oms_orders(id) ON DELETE CASCADE,
    event_type          TEXT        NOT NULL,          -- ENQUEUED / APPROVED / REJECTED / SUBMITTED / FILL / CANCEL / etc.
    prior_state         TEXT,                          -- null on the ENQUEUED row
    new_state           TEXT        NOT NULL,
    actor               TEXT        NOT NULL,          -- principal that triggered (operator, broker callback, etc.)
    detail              JSONB,                         -- broker payload, reason, etc.
    occurred_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS oms_order_events_order_id_idx
    ON oms_order_events (order_id, occurred_at_utc);


-- ── oms_fills ─────────────────────────────────────────────────────
-- One row per fill chunk. An order can fill in many partials; the
-- parent oms_orders row's filled_qty / avg_fill_price are
-- derived-from / kept-in-sync-with these rows by the OmsService.
CREATE TABLE IF NOT EXISTS oms_fills (
    id                  BIGSERIAL   PRIMARY KEY,
    order_id            UUID        NOT NULL REFERENCES oms_orders(id) ON DELETE CASCADE,
    broker_fill_id      TEXT,                          -- broker's reference, nullable for PAPER
    qty                 NUMERIC     NOT NULL CHECK (qty > 0),
    price               NUMERIC     NOT NULL,
    fee                 NUMERIC     NOT NULL DEFAULT 0,
    currency            TEXT        NOT NULL DEFAULT 'USD',
    fill_at_utc         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS oms_fills_order_id_idx
    ON oms_fills (order_id, fill_at_utc);
