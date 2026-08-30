-- 064_bar_cache_health_resolution_key.sql
--
-- bar_cache_health was keyed PRIMARY KEY (canonical, asset_class) — with NO
-- resolution — while THREE harvests write to it for the same symbol:
--
--   com.tradepro.bar-cache-harvest-daily  1d  21:30 Mon-Fri
--   com.tradepro.bar-cache-harvest-5m     5m  every 30 min
--   com.tradepro.bar-cache-harvest        1m  21:15 Mon-Fri
--
-- So they OVERWROTE each other's row per symbol and the last writer won.
-- `last_fetched_resolution` recorded which one happened to win, but it was a
-- payload column, not part of the identity — so the table could only ever hold
-- ONE lane's health per symbol.
--
-- The visible damage (15 Aug 2026): the Data screen's panel titled "Daily
-- bar-cache" was rendering the 1m harvest's numbers — a two-week coverage
-- window, coverage_partitions=1, 251 symbols instead of the daily lane's 179,
-- every row tagged us_etf (the 1m job's --asset), and a "missing days" count
-- that was really missing MINUTES. There was no way to see daily bar health at
-- all — the lane every backtest and regime call depends on.
--
-- After this migration each (symbol, asset class, resolution) keeps its own
-- row. NOTE the table is INCOMPLETE until each lane has run once: the existing
-- rows are the surviving winners of the old overwrite, so they carry whichever
-- resolution wrote last. It self-heals as each harvest runs — one nightly
-- cycle for 1d/1m, half an hour for 5m. Readers should treat "no row for this
-- resolution yet" as unknown, not as unhealthy.

-- NO BEGIN;/COMMIT; HERE. MigrationRunner wraps every migration in its own
-- transaction. This file used to open and close one of its own, which ENDED
-- the runner's transaction — so the runner's CommitAsync then threw "This
-- NpgsqlTransaction has completed", and that exception was in turn masked by a
-- failing rollback in the catch block. Net effect: 108 of 371 backend tests
-- failed with an error that pointed at nothing, for long enough that the cause
-- was assumed to be "Postgres isn't running". It was running the whole time.

-- 1. Promote resolution to a real, non-null identity column.
ALTER TABLE bar_cache_health
    ADD COLUMN IF NOT EXISTS resolution TEXT;

-- Backfill from the payload column that recorded the winning lane. Rows that
-- never recorded one become 'unknown' rather than being dropped — losing a
-- health row to a migration would look exactly like a symbol that stopped
-- being harvested.
UPDATE bar_cache_health
   SET resolution = COALESCE(NULLIF(TRIM(last_fetched_resolution), ''), 'unknown')
 WHERE resolution IS NULL;

ALTER TABLE bar_cache_health
    ALTER COLUMN resolution SET NOT NULL;

-- 2. Re-key. The old PK cannot simply be extended in place.
--    Deduplicate first: the old key guaranteed uniqueness on (canonical,
--    asset_class), so after backfill a collision on the new key is only
--    possible if that guarantee was already broken. Keep the freshest row.
DELETE FROM bar_cache_health a
      USING bar_cache_health b
      WHERE a.canonical  = b.canonical
        AND a.asset_class = b.asset_class
        AND a.resolution  = b.resolution
        AND a.updated_at_utc < b.updated_at_utc;

ALTER TABLE bar_cache_health
    DROP CONSTRAINT IF EXISTS bar_cache_health_pkey;

ALTER TABLE bar_cache_health
    ADD CONSTRAINT bar_cache_health_pkey
    PRIMARY KEY (canonical, asset_class, resolution);

-- 3. Readers filter by resolution on every query now.
CREATE INDEX IF NOT EXISTS bar_cache_health_resolution
    ON bar_cache_health (resolution, canonical);

COMMENT ON COLUMN bar_cache_health.resolution IS
    'Bar resolution this health row describes (1d/1m/5m/...). PART OF THE PRIMARY KEY since 064: the three harvest lanes previously overwrote one another.';

-- Trap the misnomer in the schema itself so the next reader is not caught by
-- it. Renaming the column would ripple through the Python payload key
-- (missingDaysCount), the C# body binding and the frontend row type; the
-- display layer now labels it correctly per row instead.
COMMENT ON COLUMN bar_cache_health.missing_days_count IS
    'MISNOMER: this is rows_expected - rows_returned from the last harvest, i.e. missing BARS, not days. On a 1d lane a bar is a session, so "days" happens to be right; on 1m/5m it counts missing MINUTES within a single session and must not be compared against a day-count threshold.';

