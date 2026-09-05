-- WHY a placement failed, on the row itself.
--
-- Owner, 5 Sep 2026: "yes but placeemnt fails then we need to see failure
-- reason".
--
-- The reason existed only in the Lambda log, which he cannot read. On screen a
-- failed placement was indistinguishable from one never attempted: both showed
-- "not placed" or, worse, an em-dash. Over the first week of live running the
-- failures were the MAJORITY of the record — resolution failures on SPY, QQQ
-- and GOLD, a margin rejection on NDX, a cancelled SPX — and not one of them
-- was visible to the person deciding whether to trust the desk.
--
-- Nullable, like every other execution column: a decision is written when it
-- is MADE, and most decisions never reach a placement at all.

ALTER TABLE strangle_decision_log
    -- The broker's own words where we have them, our refusal where we do not.
    -- Truncated by the writer, not here — a reason too long to store is still
    -- worth storing the front of.
    ADD COLUMN IF NOT EXISTS place_error TEXT;

-- Finding "what went wrong lately" should not scan the table.
CREATE INDEX IF NOT EXISTS ix_strangle_decision_place_error
    ON strangle_decision_log (as_of DESC)
    WHERE place_error IS NOT NULL;
