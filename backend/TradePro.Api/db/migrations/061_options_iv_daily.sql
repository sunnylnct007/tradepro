-- 061_options_iv_daily.sql
-- The wheel's own IV-history dataset (OAuth-only architecture, 9 Aug 2026).
--
-- WHY THIS EXISTS: IV-Rank (the wheel's vega-edge gate, BRD §5.3/§9.2) needs a
-- 52-week implied-vol history per underlying. The only feed that served that
-- history was the local IB Gateway's reqHistoricalData(OPTION_IMPLIED_
-- VOLATILITY) — retired for session-contention (owner decision: OAuth Web API
-- only, code must be runnable off-Mac). The Web API serves CURRENT IV
-- (snapshot field 7283) + 30d historic vol (7631) but no history, so we
-- HARVEST the snapshot daily into this table and compute IV-Rank from our own
-- accumulated series — window honestly labeled, never presented as 52w until
-- it IS 52w. One row per (symbol, day); re-runs upsert (last write wins —
-- intraday IV drift is fine for a daily-rank dataset).
-- Additive + idempotent so it can't disturb live data.

CREATE TABLE IF NOT EXISTS options_iv_daily (
    symbol       TEXT NOT NULL,
    trade_date   DATE NOT NULL,
    iv           DOUBLE PRECISION NOT NULL,   -- annualised implied vol, fraction (0.2538)
    hv30         DOUBLE PRECISION,            -- 30d historic vol, fraction; NULL = field dark
    source       TEXT NOT NULL DEFAULT 'ibkr_web_7283',
    captured_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_options_iv_daily_symbol
    ON options_iv_daily (symbol, trade_date DESC);
