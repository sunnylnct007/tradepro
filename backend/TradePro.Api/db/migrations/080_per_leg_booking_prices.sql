-- WHAT PRICE DID WE ACTUALLY TAKE, LEG BY LEG.
--
-- Owner, 10 Sep 2026: "we need to also record what price we took while
-- booking" / "the booking of options details need to be persisted and shown on
-- screen properly."
--
-- The execution row records credit_actual and exit_cost_actual — both TOTALS in
-- money. While a position is open the screen can show each leg's fill, because
-- the broker still holds the position. The moment it closes, that detail is
-- gone forever and only the two totals remain.
--
-- So the desk cannot answer, after the fact:
--   - which LEG made or lost the money (the put and the call routinely go
--     opposite ways -- 8 Sep BANKNIFTY was call +11,253 against put -10,840,
--     netting +412 from two legs that each moved eleven thousand)
--   - what the fill quality was: did we sell the mid, or get slipped?
--   - what we bought it back at
--
-- The MANUAL India book has carried put_entry/put_exit/call_entry/call_exit
-- since migration 070. The automated desk, which places far more often, has
-- never recorded them.
--
-- PER SHARE, matching the manual table's convention and the broker's own
-- averagePricePaid. The MONEY columns (credit_actual, exit_cost_actual) stay
-- as they are: mixing the two units is the 100x bug that stored the 2 Sep XSP
-- round trip as 0.32 when it made ~$32.

ALTER TABLE strangle_execution
    ADD COLUMN IF NOT EXISTS put_entry   NUMERIC(18,6),
    ADD COLUMN IF NOT EXISTS call_entry  NUMERIC(18,6),
    ADD COLUMN IF NOT EXISTS put_exit    NUMERIC(18,6),
    ADD COLUMN IF NOT EXISTS call_exit   NUMERIC(18,6);

COMMENT ON COLUMN strangle_execution.put_entry  IS
    'PER SHARE fill price of the short put at entry, from the position''s averagePricePaid. Money lives in credit_actual.';
COMMENT ON COLUMN strangle_execution.call_entry IS
    'PER SHARE fill price of the short call at entry.';
COMMENT ON COLUMN strangle_execution.put_exit   IS
    'PER SHARE price the short put was bought back at.';
COMMENT ON COLUMN strangle_execution.call_exit  IS
    'PER SHARE price the short call was bought back at.';

-- NOT BACKFILLED. The per-leg fills of already-closed trades were never
-- captured and cannot be reconstructed: the broker keeps no history of a
-- position that no longer exists, and deriving them from the totals would mean
-- splitting a sum by a ratio nobody measured. A NULL that says "not recorded"
-- is worth more than a number that looks like a fill and is not one.
