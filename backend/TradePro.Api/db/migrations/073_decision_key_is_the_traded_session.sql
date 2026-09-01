-- The decision log was losing a day, silently.
--
-- 1 Sep 2026. NIFTY and BANKNIFTY decisions made on 31 Aug were GONE, replaced
-- by decisions made on 1 Sep. Confirmed by decided_at_utc: both Indian markets
-- had rows decided ONLY on 1 Sep, when 31 Aug rows had been read the day before.
--
-- CAUSE: the unique key is (market, as_of, expiry_kind), and `as_of` is the
-- SETTLED SESSION THE GATE READ — not the session being traded. The two
-- diverge constantly:
--
--   31 Aug 13:xx  India: last settled = 31 Aug  -> as_of 2026-08-31
--    1 Sep 04:00  India: last settled = 31 Aug  -> as_of 2026-08-31  COLLISION
--
-- Two decisions, made on different days, for different trading sessions,
-- sharing one key. The second overwrote the first. US markets escaped only
-- because their settled session happened to advance (28 Aug -> 31 Aug).
--
-- The key is wrong in BOTH directions. It merges different trading sessions,
-- and it splits a single one: on 1 Sep the 04:00 run wrote as_of 2026-08-31 and
-- the 13:46 run wrote as_of 2026-09-01, so ONE session produced two pairs of
-- rows that look like two separate decisions.
--
-- FIX: key on exchange_date, the LOCAL SESSION DATE AT DECISION TIME — which is
-- the session the trade would actually be placed into. That keeps consecutive
-- days apart and collapses same-day re-runs to one row, latest wins. Which is
-- what an upsert on a daily decision was always supposed to mean.
--
-- as_of is KEPT as data. It records what the gate read, which is exactly the
-- input needed to re-judge the decision later — it just was never an identity.

-- 1. Collapse existing duplicates under the new key, keeping the LATEST
--    evaluation. Deleting the older row loses nothing the newer one lacks:
--    same session, same market, re-evaluated later in the day.
DELETE FROM strangle_decision_log a
 USING strangle_decision_log b
 WHERE a.market = b.market
   AND COALESCE(a.exchange_date, a.as_of) = COALESCE(b.exchange_date, b.as_of)
   AND COALESCE(a.expiry_kind, '') = COALESCE(b.expiry_kind, '')
   AND (a.decided_at_utc, a.id) < (b.decided_at_utc, b.id);

-- 2. Swap the key.
DROP INDEX IF EXISTS strangle_decision_log_uniq;

CREATE UNIQUE INDEX IF NOT EXISTS strangle_decision_log_uniq
    ON strangle_decision_log
       (market, COALESCE(exchange_date, as_of), COALESCE(expiry_kind, ''));
