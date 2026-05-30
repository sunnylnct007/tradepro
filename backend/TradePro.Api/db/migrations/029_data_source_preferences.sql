-- 029_data_source_preferences.sql
--
-- Operator-editable provider chain per (asset_class, resolution).
-- The .NET ApproveAsync path won't read this directly — it's the
-- data layer's source of truth once Phase B wires it up. Phase A
-- creates the table + the read/write endpoints + the UI editor so
-- the schema is established before code consumes it.
--
-- provider_chain is ordered: index 0 is tried first, fallback to
-- the next on failure. Empty array = "no providers configured;
-- strategy backtest at this resolution will refuse to run once
-- Phase E lands".
--
-- See CURRENT_BACKTEST_LIMITATIONS.md + ROADMAP "Trustworthy data
-- layer" for the full design + why this exists.

CREATE TABLE IF NOT EXISTS data_source_preferences (
    asset_class     TEXT NOT NULL,         -- us_equity | us_etf | fx_spot | future | option | crypto
    resolution      TEXT NOT NULL,         -- 1m | 5m | 15m | 1h | 1d | snapshot
    provider_chain  TEXT[] NOT NULL,       -- ordered list, e.g. ['yfinance', 'ig', 'finnhub']
    notes           TEXT,                  -- operator-set rationale ('IG bars cleaner for FX')
    updated_at_utc  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT NOT NULL DEFAULT 'system',
    PRIMARY KEY (asset_class, resolution)
);

-- Validate every provider name is one we know about. Add new names
-- here (and to the API's validBrokers-style list) when a new
-- provider is wired into the chain. CHECK runs per-row but covers
-- every element of the array via the unnest() expression.
ALTER TABLE data_source_preferences
    DROP CONSTRAINT IF EXISTS data_source_preferences_provider_check;
ALTER TABLE data_source_preferences
    ADD CONSTRAINT data_source_preferences_provider_check
    CHECK (
        provider_chain <@ ARRAY[
            'yfinance',
            'ig',
            'finnhub',
            't212',
            'polygon',
            'databento',
            'oanda',
            'binance'
        ]::TEXT[]
    );

-- Seed the current state honestly. yfinance is the default for
-- every (asset_class, resolution) we backtest against today. The
-- 1m row for us_equity / us_etf carries a notes field reminding
-- the operator + future investigator that this resolution is
-- depth-limited to 7 days against yfinance.
INSERT INTO data_source_preferences (asset_class, resolution, provider_chain, notes, updated_by)
VALUES
    ('us_equity', '1m', ARRAY['yfinance'],
     'yfinance 1m capped at 7 days; add IG /prices to chain in Phase B',
     'migration_029'),
    ('us_equity', '1h', ARRAY['yfinance'],
     'yfinance 1h has multi-year depth; honest for ichimoku_fx_mr-style hourly',
     'migration_029'),
    ('us_equity', '1d', ARRAY['yfinance'],
     'yfinance daily back to ~2000; the trustworthy default',
     'migration_029'),
    ('us_etf', '1m', ARRAY['yfinance'],
     'yfinance 1m capped at 7 days — affects intraday_flat / orb / vwap backtests',
     'migration_029'),
    ('us_etf', '1h', ARRAY['yfinance'], NULL, 'migration_029'),
    ('us_etf', '1d', ARRAY['yfinance'], NULL, 'migration_029'),
    ('fx_spot', '1h', ARRAY['yfinance'],
     'yfinance hourly works; FX provider drift is meaningful — IG bid/ask in Phase B for honesty',
     'migration_029'),
    ('fx_spot', '1d', ARRAY['yfinance'], NULL, 'migration_029')
ON CONFLICT (asset_class, resolution) DO NOTHING;
