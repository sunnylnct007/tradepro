-- Link a strangle DECISION to what actually executed, and how it ended.
--
-- Owner, 31 Aug 2026, asking the plainest possible question — "f the strangell
-- worked or not" — which the platform could not answer from its own records.
-- The decision log held four rows for the day, none for the US markets that
-- actually traded, and zero graded outcomes. Both numbers had to be
-- reconstructed from the broker by hand.
--
-- WHY THE DECISION LOG ALONE COULD NOT ANSWER IT. It records what we DECIDED
-- and why — which was the point, and the stand-aside rows remain the most
-- valuable in it. But it stops at the decision. Nothing recorded whether the
-- order was placed, what we were actually FILLED at, or what it cost to close.
-- A modelled Black-Scholes credit is not a traded price, so a table holding
-- only credit_modelled cannot settle whether a strategy made money.
--
-- WHY THESE COLUMNS AND NOT A SECOND TABLE. One decision produces at most one
-- position here, and the join would be on the same key the upsert already uses.
-- A separate table would add a join for no extra cardinality.
--
-- EVERY COLUMN IS NULLABLE. A decision is written when it is MADE; placement
-- happens after, and the exit hours later. Grading a row before its session
-- closes is the same lookahead this strategy has already had to be corrected
-- for, so "not yet known" must be representable.

ALTER TABLE strangle_decision_log
    -- placement
    ADD COLUMN IF NOT EXISTS placed            BOOLEAN,
    -- TRUE when only ONE leg filled. That is a NAKED short, not a strangle,
    -- and must never be averaged in with the two-leg population.
    ADD COLUMN IF NOT EXISTS partial           BOOLEAN,
    -- TRUE when the gate said stand aside and we placed anyway to capture
    -- execution. A separate population; mixing it with gated trades would
    -- corrupt the win rate the gate is judged on.
    ADD COLUMN IF NOT EXISTS shadow            BOOLEAN,
    ADD COLUMN IF NOT EXISTS broker_order_ids  TEXT,
    -- what we were ACTUALLY filled at, which no backtest can manufacture and
    -- is the single input this whole paper exercise exists to collect.
    ADD COLUMN IF NOT EXISTS credit_actual     NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS placed_at_utc     TIMESTAMPTZ,

    -- exit
    ADD COLUMN IF NOT EXISTS exit_cost_actual  NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS close_trigger     TEXT,   -- profit_target | end_of_day
    ADD COLUMN IF NOT EXISTS closed_at_utc     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS realised_pnl      NUMERIC(18,2);

-- The rows worth looking at first are the ones that actually traded.
CREATE INDEX IF NOT EXISTS ix_strangle_decision_placed
    ON strangle_decision_log (as_of DESC)
    WHERE placed IS TRUE;
