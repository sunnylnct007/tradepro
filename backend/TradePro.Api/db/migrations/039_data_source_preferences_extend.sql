-- 039_data_source_preferences_extend.sql
--
-- Extend the provider-chain seed to cover the new asset class
-- breadth (us_equity / uk_equity / fx_spot / crypto plugins shipped
-- alongside this migration). Migration 029 seeded us_equity + us_etf
-- + fx_spot at a thin set of resolutions; this migration:
--
--   * Fills in the missing resolutions for the existing asset classes
--     (5m / 15m / 30m for us_equity + us_etf, 1m / 5m / 15m / 30m for
--     fx_spot).
--
--   * Seeds the new plugins: uk_equity (yfinance — .L suffix tickers,
--     same yfinance call shape as US) + crypto (yfinance — BTC-USD
--     style tickers; BinanceProvider lands later in Phase J).
--
-- All inserts are ON CONFLICT DO NOTHING — existing rows stay
-- whatever the operator has edited them to via the cockpit
-- Preferences panel. We never overwrite operator-chosen chains.

INSERT INTO data_source_preferences
    (asset_class, resolution, provider_chain, notes, updated_by)
VALUES
    -- us_equity — fill in the intraday gaps. 5m/15m/30m yfinance has
    -- ~60-day depth, deeper than 1m's 7d but still meaningful for
    -- short-window backtests.
    ('us_equity', '5m', ARRAY['yfinance', 'ig'],
     'yfinance 5m capped at ~60 days; IG /prices fills the gap when epic is mapped',
     'migration_039'),
    ('us_equity', '15m', ARRAY['yfinance', 'ig'],
     'yfinance 15m capped at ~60 days',
     'migration_039'),
    ('us_equity', '30m', ARRAY['yfinance', 'ig'],
     'yfinance 30m capped at ~60 days',
     'migration_039'),

    -- us_etf — same intraday gaps as us_equity. IG already lives in
    -- the 1m chain (migration 033); extend that to 5/15/30m.
    ('us_etf', '5m', ARRAY['yfinance', 'ig'],
     'yfinance 5m capped at ~60 days; IG /prices fills the gap',
     'migration_039'),
    ('us_etf', '15m', ARRAY['yfinance', 'ig'],
     'yfinance 15m capped at ~60 days',
     'migration_039'),
    ('us_etf', '30m', ARRAY['yfinance', 'ig'],
     'yfinance 30m capped at ~60 days',
     'migration_039'),

    -- fx_spot — fill in the intraday gaps. yfinance covers minute-level
    -- depth for major pairs; IG /prices is the better choice on the
    -- IG-routed side (no allowance cap when the strategy is already
    -- holding an IG session). Chain ordering ('ig', 'yfinance') puts
    -- IG first because its FX bid/ask depth is materially better.
    ('fx_spot', '1m', ARRAY['ig', 'yfinance'],
     'IG /prices preferred for FX (better bid/ask); yfinance fallback',
     'migration_039'),
    ('fx_spot', '5m', ARRAY['ig', 'yfinance'], NULL, 'migration_039'),
    ('fx_spot', '15m', ARRAY['ig', 'yfinance'], NULL, 'migration_039'),
    ('fx_spot', '30m', ARRAY['ig', 'yfinance'], NULL, 'migration_039'),

    -- uk_equity — yfinance handles .L tickers natively. No IG entry
    -- yet because the IG epic map is US-only today; expand
    -- ig_epic_map.json to include UK epics before adding IG to this
    -- chain.
    ('uk_equity', '1m', ARRAY['yfinance'],
     'yfinance .L tickers; 1m capped at 7 days same as US',
     'migration_039'),
    ('uk_equity', '5m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('uk_equity', '15m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('uk_equity', '30m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('uk_equity', '1h', ARRAY['yfinance'],
     'yfinance hourly on .L tickers covers many years; ichimoku-style honest',
     'migration_039'),
    ('uk_equity', '1d', ARRAY['yfinance'],
     'yfinance daily on .L tickers back to ~2000',
     'migration_039'),

    -- crypto — yfinance handles BTC-USD style tickers. Binance is
    -- the natural primary but BinanceProvider doesn't have a Python
    -- consumer yet (only the .NET stub). When that ships, edit the
    -- chain via the cockpit Preferences panel — no migration needed.
    ('crypto', '1m', ARRAY['yfinance'],
     'yfinance 1m on crypto capped at 7 days; Binance fills the gap when consumer ships',
     'migration_039'),
    ('crypto', '5m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('crypto', '15m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('crypto', '30m', ARRAY['yfinance'], NULL, 'migration_039'),
    ('crypto', '1h', ARRAY['yfinance'],
     'yfinance hourly on crypto covers multi-year depth',
     'migration_039'),
    ('crypto', '1d', ARRAY['yfinance'],
     'yfinance daily on crypto back to mid-2010s for majors',
     'migration_039')
ON CONFLICT (asset_class, resolution) DO NOTHING;
