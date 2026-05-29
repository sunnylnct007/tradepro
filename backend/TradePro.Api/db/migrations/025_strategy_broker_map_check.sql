-- 025_strategy_broker_map_check.sql
--
-- Belt-and-braces safety on strategy_broker_map.broker. The 021
-- migration created the table without a CHECK constraint — relying
-- on the app layer to validate. Now that the UI editor is landing
-- (PR for /api/admin/strategy-broker-map GET/PUT/DELETE), a bad
-- broker string entered via the UI / a typo'd manual UPDATE / a
-- future migration with a stale value would silently corrupt the
-- dispatch chain (TradePlanEndpoints.cs reads this table to pick
-- which broker the order routes to; an unknown value falls back to
-- default_broker, masking the problem until someone notices).
--
-- The allowed values match the oms_orders.broker CHECK constraint
-- (migration 023). Keep them in sync — if you extend one, extend
-- the other in the same PR.

-- Drop any existing constraint of this name so the migration is
-- safely re-runnable in environments that ran an earlier variant.
ALTER TABLE strategy_broker_map
    DROP CONSTRAINT IF EXISTS strategy_broker_map_broker_check;

ALTER TABLE strategy_broker_map
    ADD CONSTRAINT strategy_broker_map_broker_check
    CHECK (broker IN (
        'T212_DEMO',
        'T212_LIVE',
        'IBKR_PAPER',
        'IBKR_LIVE',
        'IG_DEMO',
        'IG_LIVE',
        'PAPER'
    ));
