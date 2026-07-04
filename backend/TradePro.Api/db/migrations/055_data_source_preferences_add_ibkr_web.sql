-- 055_data_source_preferences_add_ibkr_web.sql
--
-- Adds the 'ibkr_web' provider — the IBKR OAuth Web API path via the central
-- backend endpoint (/api/integrations/ibkr/price-history), consumed by the Python
-- bar_cache provider of the same name (Option B, Data Accuracy Turnaround).
--
-- WHY a new name instead of reusing 'ibkr': the existing 'ibkr' provider is the
-- local IB GATEWAY (ib_insync), which is single-session-per-account and HANGS under
-- trader+harvester contention — so migration 045's 'ibkr'-primary rows silently fall
-- through to yfinance (BRONZE). 'ibkr_web' is the WORKING IBKR-GOOD source (no local
-- Gateway). We prefer it FIRST, keep 'ibkr' (Gateway) as a middle fallback for
-- operators running TWS, and yfinance last.
--
-- Two changes (same shape as migration 045):
--   1. Drop + recreate the provider_chain CHECK to include 'ibkr_web'.
--   2. Prepend 'ibkr_web' on us_equity + us_etf at 1d + 1h — the resolutions the web
--      provider supports and UNIVERSE_CORE needs GOOD. Only fires when the chain is
--      still the migration-045 default (operators who customised keep their choice).

-- 1. Update the CHECK constraint (add ibkr_web to the allowed set).
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
            'ibkr',
            'ibkr_web'
        ]::TEXT[]
    );

-- 2. Prefer ibkr_web first for us_equity + us_etf at 1d and 1h.
--    Only fires when the chain is still the migration-045 ['ibkr','yfinance'] default.
UPDATE data_source_preferences
SET provider_chain = ARRAY['ibkr_web', 'ibkr', 'yfinance'],
    notes          = 'ibkr_web primary (OAuth Web API, no Gateway hang); ibkr (Gateway) + yfinance fallbacks',
    updated_at_utc = NOW(),
    updated_by     = 'migration_055'
WHERE asset_class    IN ('us_equity', 'us_etf')
  AND resolution     IN ('1d', '1h')
  AND provider_chain = ARRAY['ibkr', 'yfinance'];
