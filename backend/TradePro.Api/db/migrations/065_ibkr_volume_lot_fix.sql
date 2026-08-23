-- IBKR reports historical bar volume in 100-SHARE LOTS, not shares. Stored raw
-- since the harvester was written, so every IBKR-sourced row in ibkr_price_bars
-- is 100x understated: SPY's 2026-08-21 daily bar held 589,831 against a real
-- ~59,000,000.
--
-- Why it mattered beyond cosmetics: the tradeable universe is selected on a
-- median-dollar-turnover floor, so the most liquid names in the market were
-- being excluded as "too thin to fill against" — XOM computed at $9.7M/day,
-- JNJ $8.2M, IBM $9.3M. The parquet bar-store has the same fix applied via
-- `tradepro-bar-cache-audit --fix-ibkr-volume`.
--
-- IDEMPOTENCY: guarded by a marker row, because running this twice would
-- multiply by 10,000 and be far worse than the bug it fixes.
CREATE TABLE IF NOT EXISTS schema_data_migrations (
    name        text PRIMARY KEY,
    applied_utc timestamptz NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_data_migrations
                   WHERE name = '065_ibkr_volume_lot_fix') THEN
        UPDATE ibkr_price_bars
           SET volume = volume * 100
         WHERE source = 'ibkr';
        INSERT INTO schema_data_migrations(name) VALUES ('065_ibkr_volume_lot_fix');
    END IF;
END $$;
