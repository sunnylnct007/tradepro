-- 058_oms_orders_soft_delete.sql
-- Soft-delete for OMS orders. Rows are NEVER physically removed (they're valuable
-- for later analysis / replay); instead deleted_at is stamped and all ACTIVE views
-- (order lists, positions) exclude stamped rows. Used to clear stale orders after a
-- broker paper-account RESET without losing the trade history.
-- Additive + idempotent so it can't disturb live data.

ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS deleted_at    timestamptz;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS deleted_reason text;

-- Fast "active orders" scans (the common case: deleted_at IS NULL).
CREATE INDEX IF NOT EXISTS ix_oms_orders_active
    ON oms_orders (created_at_utc DESC)
    WHERE deleted_at IS NULL;
