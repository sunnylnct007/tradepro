-- 040_broker_ticker_map_jef.sql
--
-- JEF (Jefferies Financial Group) resolves to "JEF_US_EQ" under the default
-- suffix heuristic, but T212 does NOT carry that ticker — Jefferies is listed
-- under its legacy Leucadia code "LUK_US_EQ" (confirmed via T212's instruments
-- registry: shortName "JEF", name "Jefferies Financial Group", ticker
-- LUK_US_EQ). So every ichimoku_equity BUY JEF was rejected
-- "HTTP 404: Ticker does not exist" and re-fired every session (churn).
--
-- Config fix (no hardcoding in code): map (T212, JEF) → LUK_US_EQ. The OMS's
-- ResolveBrokerSymbolAsync consults this before placing, so JEF now routes
-- correctly. Add both demo + live so it carries to real-money trading.

INSERT INTO broker_ticker_map (broker, source_ticker, broker_ticker, note)
VALUES
    ('T212_DEMO', 'JEF', 'LUK_US_EQ', 'Jefferies (ex-Leucadia) — T212 lists under LUK'),
    ('T212_LIVE', 'JEF', 'LUK_US_EQ', 'Jefferies (ex-Leucadia) — T212 lists under LUK')
ON CONFLICT (broker, source_ticker) DO UPDATE
    SET broker_ticker = EXCLUDED.broker_ticker,
        note = EXCLUDED.note,
        updated_at_utc = NOW();
