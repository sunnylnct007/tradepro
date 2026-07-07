-- 057_ibkr_price_bars.sql
-- Central OHLCV bar store — the server-side store the platform was missing.
-- Before this, the ONLY server-side price data was ig_l1_snapshots (bid/ask,
-- not bars) and bar_cache_events/health (telemetry, not bars). Intraday bars
-- lived only as parquet on the Mac. This is the durable, queryable store the
-- IBKRBarHarvester writes to (IBKR primary, Yahoo fallback) and the charts read.
--
-- Provenance is first-class: `source` records who filled each bar so the UI can
-- show GOOD (ibkr) vs BRONZE (yahoo) — loud observability, never a silent gap.
-- Idempotent + additive (CREATE ... IF NOT EXISTS) so applying it can't disturb
-- the live trading DB.

CREATE TABLE IF NOT EXISTS ibkr_price_bars (
    symbol          text        NOT NULL,
    resolution      text        NOT NULL,               -- '1m' | '5m' | '15m' | '30m' | '1h' | '1d'
    ts              timestamptz NOT NULL,               -- bar OPEN time, UTC
    open            numeric     NOT NULL,
    high            numeric     NOT NULL,
    low             numeric     NOT NULL,
    close           numeric     NOT NULL,
    volume          bigint      NOT NULL DEFAULT 0,
    source          text        NOT NULL,               -- 'ibkr' (GOOD) | 'yahoo' (BRONZE fallback)
    captured_at_utc timestamptz NOT NULL DEFAULT now(),
    -- One row per (symbol, resolution, bar). Re-harvest UPSERTs: a later IBKR
    -- fetch overwrites an earlier Yahoo fallback for the same bar (source flips
    -- bronze -> good) without creating duplicates.
    PRIMARY KEY (symbol, resolution, ts)
);

-- Fast "latest N bars for a symbol at a resolution" (chart + strategy reads).
CREATE INDEX IF NOT EXISTS ix_ibkr_price_bars_sym_res_ts
    ON ibkr_price_bars (symbol, resolution, ts DESC);

-- Fast "what did we harvest recently / which source" (observability panels).
CREATE INDEX IF NOT EXISTS ix_ibkr_price_bars_captured
    ON ibkr_price_bars (captured_at_utc DESC);
