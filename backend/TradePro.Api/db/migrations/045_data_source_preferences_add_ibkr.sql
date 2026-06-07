-- 045_data_source_preferences_add_ibkr.sql
--
-- The IBKR bar-cache provider (IBKRProvider via ib_insync) was wired
-- into the Python BarStore provider chain. Compared with existing
-- providers it offers:
--   • 1m bars going back ~1 year  (yfinance: 7 days; IG: depends on
--     weekly allowance which can be exhausted mid-session)
--   • 5m / 15m / 30m bars ~3 years
--   • Daily bars dating back decades
--   • No weekly-allowance cap (unlike IG)
--   • Live data from the same TWS connection as the paper engine's
--     IBKRBarBus — no separate auth pipeline once TWS is open
--
-- Two changes:
--   1. Drop + recreate the provider_chain CHECK constraint to include
--      'ibkr'. The previous constraint (migrations 029 + 038) only
--      allowed the original 8 provider names. Without this, a PUT
--      to /api/admin/data-trust/preferences/{…}/{…} with a chain
--      containing 'ibkr' will be accepted by the .NET validator (we
--      added it to _validProviders in DataTrustEndpoints.cs) but
--      rejected at the DB level with a CHECK violation.
--
--   2. Seed 'ibkr' as the PRIMARY source for us_equity + us_etf at
--      1m resolution (prepend; yfinance/IG remain as fallbacks).
--      The 7-day yfinance cap has been the CRITICAL data limitation
--      (CURRENT_BACKTEST_LIMITATIONS.md §L1) since Phase A; ibkr
--      closes it for operators running TWS on their Mac.
--
--      All UPDATE WHERE clauses only fire when the chain still
--      contains the expected values — operators who already customised
--      their chains via the Settings UI keep their choices. Same
--      pattern as migrations 033 + 039.

-- 1. Update the CHECK constraint.
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
            'binance',
            'ibkr'
        ]::TEXT[]
    );

-- 2a. us_equity 1m — ibkr first, then ig fallback, then yfinance.
--     Only fires when the chain is still the migration-033 default.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'ig', 'yfinance'],
    notes          = 'ibkr primary (≈1yr depth); ig /prices fallback; yfinance last-resort',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    = 'us_equity'
  AND resolution     = '1m'
  AND provider_chain = ARRAY['yfinance', 'ig'];

-- 2b. us_etf 1m — same ordering.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'ig', 'yfinance'],
    notes          = 'ibkr primary (≈1yr depth); ig /prices fallback; yfinance last-resort',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    = 'us_etf'
  AND resolution     = '1m'
  AND provider_chain = ARRAY['yfinance', 'ig'];

-- 2c. us_equity 5m / 15m / 30m — ibkr gives 3+ years vs yfinance 60d.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'ig', 'yfinance'],
    notes          = 'ibkr primary (≈3yr depth); ig + yfinance fallbacks',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    = 'us_equity'
  AND resolution     IN ('5m', '15m', '30m')
  AND provider_chain = ARRAY['yfinance', 'ig'];

-- 2d. us_etf 5m / 15m / 30m — same.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'ig', 'yfinance'],
    notes          = 'ibkr primary (≈3yr depth); ig + yfinance fallbacks',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    = 'us_etf'
  AND resolution     IN ('5m', '15m', '30m')
  AND provider_chain = ARRAY['yfinance', 'ig'];

-- 2e. 1h — ibkr gives 3.5 years; yfinance already honest at 1h
--     (multi-year) but ibkr is cleaner (no auto-adjust guessing).
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'yfinance'],
    notes          = 'ibkr primary (≈3.5yr); yfinance fallback',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    IN ('us_equity', 'us_etf')
  AND resolution     = '1h'
  AND (provider_chain = ARRAY['yfinance']
    OR provider_chain = ARRAY['yfinance', 'ig']);

-- 2f. 1d — ibkr gives decades; daily yfinance is already honest, but
--     ibkr is the cleaner primary now that the auth pipeline is live.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr', 'yfinance'],
    notes          = 'ibkr primary (decades of daily bars); yfinance fallback',
    updated_at_utc = NOW(),
    updated_by     = 'migration_045'
WHERE asset_class    IN ('us_equity', 'us_etf')
  AND resolution     = '1d'
  AND provider_chain = ARRAY['yfinance'];
