-- 036_oms_fills_l1_snapshot.sql
--
-- Phase F-2 — fill-quality capture.
--
-- Extend oms_fills with the L1 (top-of-book) snapshot taken right
-- before / at the moment we record the fill. This is the substrate
-- for an empirical slippage model (Phase F-3): "realised fill price
-- minus mid, in bps" per fill, aggregated by symbol / strategy.
--
-- Until F-2 ships these columns are NULL on every existing row —
-- backfilling is impossible because the IG snapshot was never
-- captured. New IG fills (post-F-2) populate them; the column shape
-- is the same for T212 + paper fills if/when those brokers expose a
-- snapshot at fill time, today they remain NULL there too.
--
-- All four columns nullable on purpose. The column-level documentation
-- comments are operator-facing: ``\d+ oms_fills`` in psql shows them.

ALTER TABLE oms_fills
    ADD COLUMN IF NOT EXISTS bid_at_fill        NUMERIC,
    ADD COLUMN IF NOT EXISTS ask_at_fill        NUMERIC,
    ADD COLUMN IF NOT EXISTS mid_at_fill        NUMERIC,
    ADD COLUMN IF NOT EXISTS snapshot_at_utc    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS snapshot_source    TEXT;

COMMENT ON COLUMN oms_fills.bid_at_fill IS
    'L1 bid taken from the broker right before RecordFill. NULL when the broker did not surface a snapshot (paper/T212) or fetch failed.';
COMMENT ON COLUMN oms_fills.ask_at_fill IS
    'L1 ask (IG calls it "offer"). NULL when no snapshot was captured.';
COMMENT ON COLUMN oms_fills.mid_at_fill IS
    'Mid = (bid + ask) / 2. Convenience column; derivable but cheaper to read.';
COMMENT ON COLUMN oms_fills.snapshot_at_utc IS
    'When we captured the snapshot. Tiny gap vs fill_at_utc is expected (a few hundred ms for the /markets call).';
COMMENT ON COLUMN oms_fills.snapshot_source IS
    'Where the snapshot came from: "ig_markets" today; "t212_quote" / "lightstreamer" later.';

-- Partial index — only rows with a captured snapshot. Phase F-3
-- aggregates run on these; the index keeps that query cheap.
CREATE INDEX IF NOT EXISTS oms_fills_with_snapshot
    ON oms_fills (fill_at_utc DESC)
    WHERE mid_at_fill IS NOT NULL;

COMMENT ON INDEX oms_fills_with_snapshot IS
    'Phase F-3 slippage-model queries scan this partial index instead of the full oms_fills table.';
