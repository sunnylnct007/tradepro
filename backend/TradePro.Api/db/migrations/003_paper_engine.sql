-- 003_paper_engine.sql
-- Stores that hold the output the Mac paper-engine pushes up:
-- per-session snapshots, per-backtest reports, and the strategy
-- catalog. All payloads are JSONB so the schema doesn't have to
-- evolve every time the Python side adds a field.

-- ── paper_sessions ───────────────────────────────────────────────
-- One row per (session_label) — the Mac engine writes a snapshot at
-- the end of every session. session_label is conventionally
-- "{SYMBOL}-{YYYY-MM-DD}" but the schema doesn't enforce it. The
-- summary columns are denormalised projections of the JSONB payload
-- so the List() query (most-recent N sessions) doesn't have to
-- parse JSON.
CREATE TABLE IF NOT EXISTS paper_sessions (
    session_label    TEXT        PRIMARY KEY,
    broker           TEXT        NOT NULL,
    as_of_utc        TIMESTAMPTZ NOT NULL,
    strategy_count   INT         NOT NULL DEFAULT 0,
    total_fills      INT         NOT NULL DEFAULT 0,
    payload          JSONB       NOT NULL,
    received_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS paper_sessions_received
    ON paper_sessions (received_at_utc DESC);

COMMENT ON TABLE paper_sessions IS
    'End-of-session ledger snapshot per paper session. JSONB payload contains positions + recent_fills + per-strategy P&L.';

-- ── paper_backtests ──────────────────────────────────────────────
-- Reports pushed by `tradepro-paper-backtest`. One row per report.
-- entry_count is the number of comparator entries in the payload —
-- used by the UI to badge "47 strategies tested" without loading
-- the full JSON.
CREATE TABLE IF NOT EXISTS paper_backtests (
    report_id        TEXT        PRIMARY KEY,
    kind             TEXT        NOT NULL,
    symbol           TEXT        NOT NULL,
    start_date       DATE,
    end_date         DATE,
    entry_count      INT         NOT NULL DEFAULT 0,
    payload          JSONB       NOT NULL,
    received_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS paper_backtests_received
    ON paper_backtests (received_at_utc DESC);

CREATE INDEX IF NOT EXISTS paper_backtests_symbol
    ON paper_backtests (symbol, received_at_utc DESC);

COMMENT ON TABLE paper_backtests IS
    'Per-strategy comparator backtest reports pushed from the Mac. Full results in JSONB payload; List() queries hit the denormalised columns only.';

-- ── paper_strategies ──────────────────────────────────────────────
-- Single-row table holding the latest paper-strategy catalog pushed
-- by `tradepro-paper-strategies-push`. Replaces it on every push.
CREATE TABLE IF NOT EXISTS paper_strategies (
    id          TEXT        PRIMARY KEY DEFAULT 'singleton'
                CHECK (id = 'singleton'),
    payload     JSONB       NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE paper_strategies IS
    'Latest strategy catalog from the Mac. Single-row (replaced on every push).';
