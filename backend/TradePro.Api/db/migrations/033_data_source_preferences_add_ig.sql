-- 033_data_source_preferences_add_ig.sql
--
-- Phase B-4 wired IGProvider into the trustworthy bar cache, so the
-- chain editor now has a real second option. This migration updates
-- the seeded chain for resolutions where IG meaningfully extends
-- yfinance:
--   us_etf 1m → ['yfinance', 'ig']   (yfinance capped at 7 days;
--                                      IG carries multi-year history)
--   us_etf 1h → ['yfinance', 'ig']   (yfinance carries it, but IG
--                                      provides a redundant source —
--                                      cheap fallback if yfinance 429s)
--
-- The WHERE clauses are deliberate: each only fires when the chain
-- still matches the legacy ['yfinance'] seed value, so an operator
-- who already customised the chain via the Settings UI keeps their
-- choice. Same pattern migration 026 used for the broker map.
--
-- After this lands, the BarStore chain editor in Settings shows
-- both providers as options; an operator can reorder them, drop one,
-- or fall back to yfinance-only without a migration. The trustworthy
-- data layer's §L1 (CRITICAL — intraday backtests fictional past
-- 7 days) is closed for the symbols where IG epics are populated.
-- Symbols whose IG epic is still null silently fall through to
-- yfinance — the provider chain absorbs that cleanly.

UPDATE data_source_preferences
SET provider_chain = ARRAY['yfinance', 'ig'],
    notes          = 'yfinance primary; IG /prices fallback for >7d history',
    updated_at_utc = NOW(),
    updated_by     = 'migration_033'
WHERE asset_class = 'us_etf'
  AND resolution  = '1m'
  AND provider_chain = ARRAY['yfinance'];

UPDATE data_source_preferences
SET provider_chain = ARRAY['yfinance', 'ig'],
    notes          = 'yfinance primary; IG /prices fallback if rate-limited',
    updated_at_utc = NOW(),
    updated_by     = 'migration_033'
WHERE asset_class = 'us_etf'
  AND resolution  = '1h'
  AND provider_chain = ARRAY['yfinance'];

-- us_equity 1m similarly benefits (when the us_equity plugin lands).
UPDATE data_source_preferences
SET provider_chain = ARRAY['yfinance', 'ig'],
    notes          = 'yfinance primary; IG /prices fallback for >7d history',
    updated_at_utc = NOW(),
    updated_by     = 'migration_033'
WHERE asset_class = 'us_equity'
  AND resolution  = '1m'
  AND provider_chain = ARRAY['yfinance'];
