-- WHAT THE SIGNAL SAID, carried on the order that acted on it.
--
-- Owner, 29 Aug 2026: the paper sleeve should "store our auto trading ... to
-- get the daily trading order data etc for back test purpose".
--
-- The table already held what we DID -- symbol, side, qty, fill price, broker
-- id. It held nothing about what we INTENDED, so the questions that make live
-- data worth keeping were all unanswerable:
--
--   * what did we mean to pay, and what did we actually pay?  (forward-test F3)
--   * did the fill land where the screen published it would?  (F1)
--   * which setup produced this trade, and how stretched was it?
--
-- Without these the record is a P&L log, not a backtest input. Migration 009
-- noted signal_id/decision_id as deferred ("those tables aren't a hard dep
-- yet"); this takes the cheaper route of denormalising the few numbers a study
-- actually needs onto the order itself, so no join and no second table can
-- drift out of step with it.
--
-- All NULLABLE. Orders placed by hand, by the reconciler, or by any strategy
-- that has no published setup simply leave them empty -- and a NULL here means
-- "no signal was recorded", which is a different fact from "the signal was at
-- zero". That distinction is the whole reason yesterday's zero-price fills
-- went unnoticed for two months.

ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS signal_bar          date;
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS signal_ref_price    numeric(18,6);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS signal_target_price numeric(18,6);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS signal_stop_price   numeric(18,6);
ALTER TABLE oms_orders ADD COLUMN IF NOT EXISTS signal_meta         jsonb;

COMMENT ON COLUMN oms_orders.signal_bar IS
  'The SETTLED session the signal was computed on -- not the day the order was placed. The two differ by design: the rule signals on a settled close and enters at the next open.';
COMMENT ON COLUMN oms_orders.signal_ref_price IS
  'The price the signal was computed against. Entry slippage is measured against THIS, never against the fill.';
COMMENT ON COLUMN oms_orders.signal_target_price IS 'Published target at signal time.';
COMMENT ON COLUMN oms_orders.signal_stop_price IS
  'Published stop at signal time. Distinct from stop_price, which is an ORDER TYPE parameter for STP/STP_LMT orders and is null on a market order.';
COMMENT ON COLUMN oms_orders.signal_meta IS
  'Free-form setup detail (sigma from the mean, ATR%, rank, regime). jsonb so a study can reach in without a schema change every time a rule gains a field.';

-- Studies filter on "orders that carry a recorded setup", so index that.
CREATE INDEX IF NOT EXISTS idx_oms_orders_signal_bar
    ON oms_orders(signal_bar) WHERE signal_bar IS NOT NULL;
