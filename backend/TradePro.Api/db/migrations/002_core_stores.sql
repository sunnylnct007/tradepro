-- 002_core_stores.sql
-- The simple key-value-ish stores: settings (single-row), watchlists,
-- heartbeats, and the pending-orders queue for T212 manual placement.
-- Designed to replace four existing stores 1:1 so the .NET DI swap is
-- a one-line change.

-- ── settings ─────────────────────────────────────────────────────
-- Single-row table for application-wide settings. We use a sentinel
-- primary key ("singleton") so there can only ever be one row.
-- The payload is JSONB so we can evolve AppSettings without schema
-- changes for every new field.
CREATE TABLE IF NOT EXISTS settings (
    id          TEXT        PRIMARY KEY DEFAULT 'singleton'
                CHECK (id = 'singleton'),
    payload     JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE settings IS
    'Application-wide settings (sentiment thresholds, placement mode, etc.). Single-row enforced by CHECK constraint.';

-- ── watchlists ───────────────────────────────────────────────────
-- One row per named watchlist. Items live in a child table so we can
-- query / mutate individual symbols without rewriting the whole list.
CREATE TABLE IF NOT EXISTS watchlists (
    name        TEXT        PRIMARY KEY,
    currency    TEXT        NOT NULL DEFAULT 'GBP',
    region      TEXT        NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_name  TEXT NOT NULL REFERENCES watchlists(name) ON DELETE CASCADE,
    symbol          TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT '',
    position        INT  NOT NULL DEFAULT 0,
    PRIMARY KEY (watchlist_name, symbol)
);

CREATE INDEX IF NOT EXISTS watchlist_items_by_pos
    ON watchlist_items (watchlist_name, position);

COMMENT ON TABLE watchlists IS 'Named lists of symbols the user scans together.';
COMMENT ON TABLE watchlist_items IS 'Symbols in a watchlist. Ordered by position for stable UI rendering.';

-- ── heartbeats ───────────────────────────────────────────────────
-- The Mac worker pings here every cycle so the UI can show "worker
-- alive, last seen 2 min ago". Single-host today; if we ever go
-- multi-tenant the (host, last_seen_at) tuple gives the natural query.
CREATE TABLE IF NOT EXISTS heartbeats (
    host          TEXT        PRIMARY KEY,
    payload       JSONB       NOT NULL,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE heartbeats IS
    'Last seen heartbeat per worker host. Updated in place on every ping; we do not keep history (use the events table for that).';

-- ── pending_orders ────────────────────────────────────────────────
-- The T212 manual-placement queue. State machine:
--   pending → placed | failed | rejected
-- Terminal rows stay forever for audit; we evict the oldest *terminal*
-- row when the table grows past max_pending_orders (configurable in
-- the app, default 200). The state column is TEXT + CHECK rather than
-- a native ENUM so adding a new state doesn't require ALTER TYPE.
CREATE TABLE IF NOT EXISTS pending_orders (
    order_id            TEXT        PRIMARY KEY,
    broker              TEXT        NOT NULL,
    broker_mode         TEXT        NOT NULL,
    strategy_id         TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    t212_ticker         TEXT        NOT NULL DEFAULT '',
    side                TEXT        NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity            INT         NOT NULL,
    order_type          TEXT        NOT NULL DEFAULT 'MARKET',
    tag                 TEXT,
    suggested_at_utc    TIMESTAMPTZ NOT NULL,
    bar_at_emit_close   DOUBLE PRECISION,
    bar_at_emit_time    TIMESTAMPTZ,
    state               TEXT        NOT NULL DEFAULT 'Pending'
                        CHECK (state IN ('Pending', 'Placed', 'Failed', 'Rejected')),
    received_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at_utc      TIMESTAMPTZ,
    broker_order_id     BIGINT,
    broker_status       TEXT,
    rejection_reason    TEXT,
    error               TEXT,
    response_body       TEXT
);

CREATE INDEX IF NOT EXISTS pending_orders_state_received
    ON pending_orders (state, received_at_utc DESC);

CREATE INDEX IF NOT EXISTS pending_orders_strategy
    ON pending_orders (strategy_id, received_at_utc DESC);

COMMENT ON TABLE pending_orders IS
    'T212 manual-placement queue. State machine pending -> placed/failed/rejected. Terminal rows kept for audit.';
COMMENT ON COLUMN pending_orders.broker_order_id IS
    'T212 order id once placed. BIGINT because T212 ids exceed Int32 range.';
