-- 005_event_sourcing.sql
-- The unicorn-tier additions per VISION.md: append-only order/fill
-- log, generic domain event stream, versioned strategy registry,
-- and per-backtest run history. Nothing here replaces a pre-existing
-- store — these are new capabilities that downstream phases (risk
-- engine, real-time event stream, walk-forward backtest) build on.

-- ── strategy_versions ────────────────────────────────────────────
-- The registry. (name, version) is the natural key; code_hash lets
-- us detect drift between what's registered and what's actually
-- running. params_schema documents the strategy's parameters; the
-- runtime can validate strategy_runs.params against it.
CREATE TABLE IF NOT EXISTS strategy_versions (
    name              TEXT        NOT NULL,
    version           TEXT        NOT NULL,
    code_hash         TEXT        NOT NULL,
    params_schema     JSONB       NOT NULL DEFAULT '{}'::JSONB,
    description       TEXT        NOT NULL DEFAULT '',
    layer             TEXT        NOT NULL CHECK (layer IN ('signal', 'paper', 'scorer')),
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deprecated_at     TIMESTAMPTZ,
    PRIMARY KEY (name, version)
);

CREATE INDEX IF NOT EXISTS strategy_versions_layer
    ON strategy_versions (layer, name);

COMMENT ON TABLE strategy_versions IS
    'Versioned strategy registry. layer = signal (.NET daily), paper (Python intraday), or scorer (horizon composites). code_hash detects drift; params_schema documents what each strategy accepts.';

-- ── orders ───────────────────────────────────────────────────────
-- APPEND-ONLY log of every order intent emitted by any strategy in
-- any mode (backtest, paper-trade auto, paper-trade manual, live).
-- Once written, never updated — terminal state lives in pending_orders
-- (for the manual queue) or is derived from the order's fills.
--
-- decision_trace is the unicorn-tier requirement from VISION.md
-- principle 3: every order carries the facts that produced it.
-- Strategy version + params_hash gives bit-for-bit reproducibility.
CREATE TABLE IF NOT EXISTS orders (
    order_id            TEXT        PRIMARY KEY,
    correlation_id      TEXT,
    strategy_name       TEXT        NOT NULL,
    strategy_version    TEXT        NOT NULL,
    params_hash         TEXT        NOT NULL,
    mode                TEXT        NOT NULL CHECK (mode IN ('backtest', 'paper_auto', 'paper_manual', 'live')),
    broker              TEXT        NOT NULL,
    symbol              TEXT        NOT NULL,
    side                TEXT        NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity            NUMERIC(18, 8) NOT NULL,
    order_type          TEXT        NOT NULL DEFAULT 'MARKET',
    limit_price         NUMERIC(18, 8),
    stop_price          NUMERIC(18, 8),
    bar_at_emit_close   NUMERIC(18, 8),
    bar_at_emit_time    TIMESTAMPTZ,
    decision_trace      JSONB       NOT NULL DEFAULT '[]'::JSONB,
    tag                 TEXT,
    emitted_at_utc      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Risk-engine decision; null until risk has evaluated.
    risk_decision       TEXT        CHECK (risk_decision IN ('approve', 'reject') OR risk_decision IS NULL),
    risk_reason         TEXT,
    risk_decided_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS orders_strategy_emitted
    ON orders (strategy_name, strategy_version, emitted_at_utc DESC);

CREATE INDEX IF NOT EXISTS orders_symbol_emitted
    ON orders (symbol, emitted_at_utc DESC);

CREATE INDEX IF NOT EXISTS orders_correlation
    ON orders (correlation_id)
    WHERE correlation_id IS NOT NULL;

COMMENT ON TABLE orders IS
    'Append-only log of every order intent. Never updated; risk_decision is the only post-emit mutation, scoped to add-only fields. Backtest, paper, and live all write here.';
COMMENT ON COLUMN orders.decision_trace IS
    'Strategy facts at emit time — RSI, SMA distances, range percentile, regime, etc. Stored as a JSONB array of {factor, value, contribution}. Lets every order trace back to "why".';

-- ── fills ────────────────────────────────────────────────────────
-- APPEND-ONLY. Every execution recorded against an order, including
-- partial fills. Multiple fills per order allowed. position state is
-- derived from orders + fills via the positions view below.
CREATE TABLE IF NOT EXISTS fills (
    fill_id           BIGSERIAL   PRIMARY KEY,
    order_id          TEXT        NOT NULL REFERENCES orders(order_id),
    broker_order_id   TEXT,
    fill_qty          NUMERIC(18, 8) NOT NULL,
    fill_price        NUMERIC(18, 8) NOT NULL,
    commission        NUMERIC(18, 8) NOT NULL DEFAULT 0,
    filled_at_utc     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bar_at_fill_close NUMERIC(18, 8),
    bar_at_fill_time  TIMESTAMPTZ,
    raw_response      JSONB
);

CREATE INDEX IF NOT EXISTS fills_order ON fills (order_id);
CREATE INDEX IF NOT EXISTS fills_filled_at ON fills (filled_at_utc DESC);

COMMENT ON TABLE fills IS
    'Append-only execution log. One row per fill — multiple rows per order if partially filled. positions view aggregates these.';

-- ── positions (view) ─────────────────────────────────────────────
-- Derived state — not a table. Aggregates open quantity per
-- (strategy_name, strategy_version, symbol, mode) from orders + fills.
-- Refreshed automatically on read (it's a regular view, not a
-- materialised one — at our volume the join is sub-millisecond).
CREATE OR REPLACE VIEW positions AS
SELECT
    o.strategy_name,
    o.strategy_version,
    o.symbol,
    o.mode,
    o.broker,
    SUM(
        CASE WHEN o.side = 'BUY' THEN COALESCE(f.fill_qty, 0)
             ELSE -COALESCE(f.fill_qty, 0)
        END
    ) AS net_quantity,
    -- Volume-weighted average entry price (BUY fills only)
    CASE
        WHEN SUM(CASE WHEN o.side = 'BUY' THEN COALESCE(f.fill_qty, 0) ELSE 0 END) = 0
            THEN NULL
        ELSE SUM(CASE WHEN o.side = 'BUY' THEN COALESCE(f.fill_qty, 0) * COALESCE(f.fill_price, 0) ELSE 0 END)
             / NULLIF(SUM(CASE WHEN o.side = 'BUY' THEN COALESCE(f.fill_qty, 0) ELSE 0 END), 0)
    END AS avg_entry_price,
    MAX(f.filled_at_utc) AS last_fill_at_utc,
    COUNT(f.fill_id)     AS fill_count
FROM orders o
LEFT JOIN fills f ON f.order_id = o.order_id
WHERE o.risk_decision IS NULL OR o.risk_decision = 'approve'
GROUP BY o.strategy_name, o.strategy_version, o.symbol, o.mode, o.broker
HAVING SUM(
    CASE WHEN o.side = 'BUY' THEN COALESCE(f.fill_qty, 0)
         ELSE -COALESCE(f.fill_qty, 0)
    END
) <> 0;

COMMENT ON VIEW positions IS
    'Open positions per (strategy, version, symbol, mode). Computed from orders + fills on every read. Net-zero positions hidden.';

-- ── strategy_runs ────────────────────────────────────────────────
-- One row per backtest run. The stats blob is the full Sharpe / CAGR /
-- max DD / regime breakdown JSON. Lets us answer "what did strategy X
-- look like on AAPL six months ago" without re-running the backtest.
CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id            BIGSERIAL   PRIMARY KEY,
    strategy_name     TEXT        NOT NULL,
    strategy_version  TEXT        NOT NULL,
    symbol            TEXT        NOT NULL,
    params            JSONB       NOT NULL DEFAULT '{}'::JSONB,
    params_hash       TEXT        NOT NULL,
    from_date         DATE        NOT NULL,
    to_date           DATE        NOT NULL,
    stats             JSONB       NOT NULL,
    bars              INT         NOT NULL,
    ran_at_utc        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS strategy_runs_lookup
    ON strategy_runs (strategy_name, strategy_version, symbol, from_date, to_date);

CREATE INDEX IF NOT EXISTS strategy_runs_recent
    ON strategy_runs (ran_at_utc DESC);

COMMENT ON TABLE strategy_runs IS
    'Every backtest run, keyed by (strategy, version, symbol, params_hash, window). Lets us reproduce past signals and feed Lens 1/2 of EVALUATION.md.';

-- ── events ───────────────────────────────────────────────────────
-- APPEND-ONLY generic domain event log. Seeds the Phase 7 real-time
-- event stream. Every meaningful domain action (order emitted, fill
-- received, risk approval/rejection, strategy version registered,
-- heartbeat received, regime shifted) writes a row.
--
-- payload shape varies by event_type; the consumer is expected to
-- pattern-match on event_type before reading payload. seq is a
-- monotonic ordering inside event_type (use it for resumable
-- subscriptions).
CREATE TABLE IF NOT EXISTS events (
    seq           BIGSERIAL   PRIMARY KEY,
    event_type    TEXT        NOT NULL,
    aggregate_id  TEXT,
    payload       JSONB       NOT NULL,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS events_type_occurred
    ON events (event_type, occurred_at DESC);

CREATE INDEX IF NOT EXISTS events_aggregate
    ON events (aggregate_id, occurred_at DESC)
    WHERE aggregate_id IS NOT NULL;

COMMENT ON TABLE events IS
    'Append-only domain event log. Foundation for the Phase 7 SSE stream. Anything worth subscribing to in the future writes a row here.';
