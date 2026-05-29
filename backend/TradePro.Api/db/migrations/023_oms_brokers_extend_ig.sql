-- 023_oms_brokers_extend_ig.sql
--
-- Extend the oms_orders.broker CHECK constraint to include IG_DEMO and
-- IG_LIVE. Was previously T212_DEMO / T212_LIVE / IBKR_PAPER /
-- IBKR_LIVE / PAPER only; without this every IG-routed order errors at
-- enqueue with "oms_orders_broker_check" violation, including the
-- /api/admin/ig/smoke-order chain we use to verify IG end-to-end.

ALTER TABLE oms_orders
    DROP CONSTRAINT IF EXISTS oms_orders_broker_check;

ALTER TABLE oms_orders
    ADD CONSTRAINT oms_orders_broker_check
    CHECK (broker IN (
        'T212_DEMO',
        'T212_LIVE',
        'IBKR_PAPER',
        'IBKR_LIVE',
        'IG_DEMO',
        'IG_LIVE',
        'PAPER'
    ));
