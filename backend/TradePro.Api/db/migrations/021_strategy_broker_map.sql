-- 021_strategy_broker_map.sql
--
-- Strategy → broker mapping so multiple strategies can run side-by-side
-- with different brokers (e.g. ichimoku_equity → IG_DEMO for US equities,
-- ichimoku_fx_mr → IG_DEMO for FX, future indian_etf_sleeve → IG_LIVE
-- once IG India is sorted). Falls back to app_settings_kv.default_broker
-- when a strategy is unmapped, so existing flows keep working.

CREATE TABLE IF NOT EXISTS strategy_broker_map (
    strategy_id   TEXT PRIMARY KEY,
    broker        TEXT NOT NULL,           -- T212_DEMO | T212_LIVE | IG_DEMO | IG_LIVE | PAPER
    account_id    TEXT,                    -- optional broker-side account id (IG multi-account)
    note          TEXT,
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by    TEXT NOT NULL DEFAULT 'system'
);

-- Seed sensible defaults — point both validated strategies at IG_DEMO
-- once IG creds are loaded; T212_DEMO remains the fallback via
-- default_broker for unmapped strategies.
INSERT INTO strategy_broker_map (strategy_id, broker, note, updated_by)
VALUES
    ('ichimoku_equity', 'IG_DEMO', 'US equity sleeve via IG demo', 'migration'),
    ('ichimoku_fx_mr',  'IG_DEMO', 'G10 FX intraday via IG demo (when FX wired)', 'migration')
ON CONFLICT (strategy_id) DO NOTHING;
