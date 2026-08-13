-- 063_option_quotes_and_straddle.sql
-- Own-collected option market data (owner directive 13 Aug 2026: "we should
-- start storing data from now on for future needs").
--
-- WHY: IBKR serves NO history for expired option contracts, so any future
-- options backtest (the earnings-straddle spec §4.1 most urgently) can only
-- be honest on quotes WE captured while the contracts were alive. Every G3
-- chain fetch — screen, symbol lab, straddle scanner — upserts its legs
-- here: one row per (contract, capture day), last write of the day wins.
-- ~82 names × ~40 legs × 3-4 runs/day ≈ a few k rows/day — trivial for PG,
-- priceless in a year.

CREATE TABLE IF NOT EXISTS option_quote_daily (
    symbol        TEXT NOT NULL,
    expiry        DATE NOT NULL,
    strike        DOUBLE PRECISION NOT NULL,
    "right"       TEXT NOT NULL,               -- 'P' / 'C'
    capture_date  DATE NOT NULL,
    bid           DOUBLE PRECISION,
    ask           DOUBLE PRECISION,
    delta         DOUBLE PRECISION,
    iv            DOUBLE PRECISION,            -- fraction (0.42), NULL = not served
    open_interest INTEGER,
    spot          DOUBLE PRECISION,            -- underlying at capture
    source        TEXT NOT NULL DEFAULT 'g3_chain',
    captured_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, expiry, strike, "right", capture_date)
);

CREATE INDEX IF NOT EXISTS idx_option_quote_daily_sym_cap
    ON option_quote_daily (symbol, capture_date DESC);

-- Straddle scanner output (spec Part B §2 — OBSERVATIONAL ONLY until its
-- §4 gates clear on forward-collected data; nothing here is tradeable).
-- One row per (symbol, report_date, capture day): the implied move priced
-- that day vs the name's own historical print behaviour.
CREATE TABLE IF NOT EXISTS straddle_scan (
    symbol            TEXT NOT NULL,
    report_date       DATE NOT NULL,           -- the upcoming print scanned against
    capture_date      DATE NOT NULL,
    expiry            DATE,                    -- nearest expiry after the print
    spot              DOUBLE PRECISION,
    straddle_mid      DOUBLE PRECISION,        -- ATM call mid + put mid
    implied_move_pct  DOUBLE PRECISION,
    realized_median_pct DOUBLE PRECISION,      -- median |T+1/T-1 - 1| over n_prints
    realized_p25_pct  DOUBLE PRECISION,
    realized_p75_pct  DOUBLE PRECISION,
    n_prints          INTEGER,
    edge_ratio        DOUBLE PRECISION,        -- realized_median / implied
    iv_hv_ratio       DOUBLE PRECISION,
    iv_pctile         DOUBLE PRECISION,        -- own-store IV percentile, NULL while immature
    per_leg_oi_min    INTEGER,
    per_leg_spread_pct_max DOUBLE PRECISION,
    candidate         BOOLEAN NOT NULL DEFAULT FALSE,  -- passed all live gates (§2.4)
    gates             JSONB,                   -- each gate's value + verdict, auditable
    captured_at_utc   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, report_date, capture_date)
);

CREATE INDEX IF NOT EXISTS idx_straddle_scan_capture
    ON straddle_scan (capture_date DESC);
