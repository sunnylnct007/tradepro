-- 042_broker_ticker_map_isin.sql
--
-- Add an ISIN column to broker_ticker_map. ISIN is the durable,
-- broker-INDEPENDENT identity for a security — the same instrument has
-- the same ISIN on T212, IG, IBKR, Yahoo. The new instrument-identity
-- builder (BrokerTickerMapBuilder) records the matched instrument's ISIN
-- here so that, once universe-side ISINs are backfilled, cross-broker
-- mapping can pivot on ISIN instead of fragile ticker/name heuristics.
--
-- Nullable + IF NOT EXISTS so this is a safe, idempotent forward-only
-- migration over the existing (broker, source_ticker)-keyed table.

ALTER TABLE broker_ticker_map ADD COLUMN IF NOT EXISTS isin TEXT;
