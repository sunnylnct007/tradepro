-- An execution row is a ROUND-TRIP. A refusal is not one.
--
-- 078 gave executions their own identity, and the writer then inserted a row
-- for every POST that was not a close. But record_execution POSTs for EVERY
-- market on EVERY run, including the ones that never traded:
--
--   BANKNIFTY  market is not paper-tradeable
--   NDX        market is not paper-tradeable
--   GOLD       could not resolve one or both contracts -- NOTHING was placed
--
-- 53 rows of that against 11 real placements. Those refusals are DECISIONS;
-- place_error already records them on the decision row, which is where a
-- reader looks for why something did not trade.
--
-- The damage beyond noise: entry_seq stopped meaning what it says. The 04:11
-- India run created a phantom entry 1 for SPX, so the real US placement at
-- 13:54 was recorded as entry 2 of that session. The number is supposed to
-- answer WHICH ROUND-TRIP -- and there had only ever been one.
--
-- Every reader filters `placed IS TRUE`, so no P&L or statistic was ever
-- wrong. This corrects the table itself.

DELETE FROM strangle_execution WHERE placed IS NOT TRUE;

-- Renumber densely, oldest placement first, so entry_seq once again reads as
-- "the Nth round-trip of this session". Gaps would be harmless but a 2 with no
-- 1 invites exactly the question that cost an hour tonight.
WITH renumbered AS (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY market, session, expiry_kind
                              ORDER BY placed_at_utc NULLS LAST, id) AS seq
      FROM strangle_execution)
UPDATE strangle_execution e
   SET entry_seq = r.seq
  FROM renumbered r
 WHERE e.id = r.id
   AND e.entry_seq IS DISTINCT FROM r.seq;
