-- 048_universe_symbols_isin.sql
--
-- Add an ISIN column to universe_symbols. ISIN is the durable,
-- broker-INDEPENDENT identity for a security — the same instrument has
-- the same ISIN on T212, IG, IBKR, Yahoo. The universe is built from
-- Wikipedia/curated lists that carry only a human ticker + name, so the
-- ISIN starts null and is BACKFILLED from a broker's own instrument
-- registry the first time BrokerTickerMapBuilder resolves the symbol
-- (exact/short-name/ISIN match). Once populated, cross-broker mapping
-- pivots on this ISIN (see broker_ticker_map.isin, migration 042) instead
-- of fragile per-broker ticker/name heuristics — so a re-tickered or
-- multi-listed name resolves on every broker that lists the same ISIN.
--
-- Nullable + IF NOT EXISTS so this is a safe, idempotent forward-only
-- migration over the existing (universe_name, ticker)-keyed table.

ALTER TABLE universe_symbols ADD COLUMN IF NOT EXISTS isin TEXT;

CREATE INDEX IF NOT EXISTS idx_universe_symbols_isin
    ON universe_symbols(isin)
    WHERE isin IS NOT NULL;
