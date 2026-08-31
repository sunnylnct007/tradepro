-- Record the POST-OPEN volatility reading beside the settled one.
--
-- Owner, 31 Aug 2026: the gate reads the last SETTLED close, which over a
-- weekend can be three days old. His point: "instead of saying no signal we shd
-- be looking after market open".
--
-- HE IS RIGHT THAT IT IS NOT LOOKAHEAD. This morning's bug was using the day's
-- CLOSING vol to decide at the OPEN — reading the future. But the job runs at
-- 13:45 UTC, fifteen minutes after the US open; by then the vol index has been
-- trading and that number legitimately exists. Three options, only one wrong:
--
--     prior settled close   knowable YES   matches the evidence YES (but stale)
--     vol at decision time  knowable YES   matches the evidence NO  (untested)
--     that day's vol close  knowable NO    <- this was the bug
--
-- So the middle option is legitimate and probably better, and entirely
-- unmeasured. Rather than switch the gate on reasoning — the same mistake as
-- setting a threshold by judgement — BOTH readings are recorded every day and
-- the question settles itself on real data in a couple of months.
--
-- The gate is NOT changed. vol_at_decision is recorded and ignored.

ALTER TABLE strangle_decision_log
    ADD COLUMN IF NOT EXISTS vol_at_decision  NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS vol_at_decision_utc TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS data_source      TEXT;

COMMENT ON COLUMN strangle_decision_log.vol_at_decision IS
    'The volatility index AT DECISION TIME (post-open), recorded but NOT used by the gate. Paired with vol_index (the settled close the gate actually read) so the two can be compared over a real sample before either is trusted.';
COMMENT ON COLUMN strangle_decision_log.data_source IS
    'Which provider answered, per series — e.g. "price=bar_cache(ibkr), vol=yahoo". IBKR serves no index price history on this account, so every volatility gate is Yahoo-sourced by necessity, and that is recorded rather than assumed.';
