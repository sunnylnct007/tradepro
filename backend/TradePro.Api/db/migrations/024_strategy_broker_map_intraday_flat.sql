-- 024_strategy_broker_map_intraday_flat.sql
--
-- Add the `intraday_flat` strategy (PR #28 / commit 6f58920) to the
-- strategy → broker mapping introduced in 021. intraday_flat is a
-- long-only EOD-flat intraday strategy on US ETFs, designed from day
-- one to route via IG demo: orders stamp broker_label="IG_DEMO" and
-- instrument_id=<IG epic>, and the .NET ApproveAsync path dispatches
-- them through IGClient.PlaceMarketOrderAsync.
--
-- ON CONFLICT DO NOTHING is intentional. If an operator has already
-- overridden the mapping (via a future admin endpoint / direct UPDATE)
-- this migration must not clobber that choice. To force-update for
-- this single row, run manually:
--   UPDATE strategy_broker_map SET broker = 'IG_DEMO',
--          note = 'US ETF intraday EOD-flat via IG demo',
--          updated_by = 'operator'
--    WHERE strategy_id = 'intraday_flat';
--
-- Future strategies should add their own incremental migration here
-- rather than back-editing 021_strategy_broker_map.sql — keeps the
-- audit trail clean and makes rollback per-strategy possible.

INSERT INTO strategy_broker_map (strategy_id, broker, note, updated_by)
VALUES
    ('intraday_flat', 'IG_DEMO',
     'US ETF intraday EOD-flat via IG demo (PR #28)',
     'migration')
ON CONFLICT (strategy_id) DO NOTHING;
