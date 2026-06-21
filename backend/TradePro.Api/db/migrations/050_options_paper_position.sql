-- 050_options_paper_position.sql
--
-- Paper wheel positions — the BRD Phase-1 record. Every paper CSP entry is
-- logged with the full decision context (strike, premium, delta, IV-Rank, cash
-- secured, expiry) + the risk-engine verdict at entry, then tracked through the
-- wheel state machine (SHORT_PUT_OPEN → ASSIGNED → COVERED_CALL_OPEN → CLOSED).
-- This is the auditable per-cycle ledger the BRD §11 requires before live capital.

CREATE TABLE IF NOT EXISTS options_paper_position (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    symbol          TEXT NOT NULL,
    structure       TEXT NOT NULL DEFAULT 'CASH_SECURED_PUT',
    state           TEXT NOT NULL DEFAULT 'SHORT_PUT_OPEN',
    -- entry economics
    strike          NUMERIC,
    expiry          DATE,
    dte             INT,
    delta           NUMERIC,
    iv_rank         NUMERIC,
    premium         NUMERIC,            -- per share (USD)
    contracts       INT NOT NULL DEFAULT 1,
    cash_secured_gbp NUMERIC,
    regime          TEXT,
    -- lifecycle
    opened_at_utc   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at_utc   TIMESTAMPTZ,
    realised_pnl_gbp NUMERIC,
    notes           TEXT,
    risk_decision   JSONB,              -- the risk-engine verdict at entry (audit)
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_options_paper_position_state
    ON options_paper_position(state, opened_at_utc DESC);
