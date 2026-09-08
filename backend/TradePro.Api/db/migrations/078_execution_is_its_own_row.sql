-- A DECISION IS ONE PER SESSION. AN EXECUTION IS ONE PER ROUND-TRIP.
--
-- Migration 073 keyed the decision log on the traded session and said so
-- plainly: it "collapses same-day re-runs to one row, latest wins". That was
-- CORRECT. A same-day re-run re-evaluates one decision; it is not a new fact.
--
-- Migration 072 then added ten EXECUTION columns to that same row -- placed,
-- credit_actual, placed_at_utc, closed_at_utc, realised_pnl and the rest -- and
-- the writer updates them in place with COALESCE(@x, x). So a second entry in a
-- session overwrites the first, and COALESCE keeps whatever the second call did
-- not send.
--
-- 8 Sep 2026 made it visible. Two round-trips in one session:
--   13:53 entered, 14:00 flattened by the stale_overnight defect
--   15:39 re-entered, still open
-- The SPX row ended up carrying placed_at_utc 15:41 with closed_at_utc 14:00 --
-- a trade that closed 101 minutes BEFORE it was placed -- and credit_actual
-- from the second entry beside realised_pnl from the first. Two trades wearing
-- one row. Every figure computed over it was wrong, and the open legs could not
-- be given a placement time because their session row was already stamped
-- closed by a different trade.
--
-- The fix is not a different key on the same row. It is that these are two
-- different things and always were.

CREATE TABLE IF NOT EXISTS strangle_execution (
    id                BIGSERIAL PRIMARY KEY,

    -- Identity of the DECISION this came from: the same triple 073 settled on.
    market            TEXT        NOT NULL,
    session           DATE        NOT NULL,
    expiry_kind       TEXT        NOT NULL DEFAULT '',

    -- Which round-trip within that session. 1, 2, 3... This is the column the
    -- decision row could never have, and its absence is the whole bug.
    entry_seq         INT         NOT NULL,

    -- The strikes ACTUALLY traded. On the decision row these describe what was
    -- chosen; a re-entry can choose differently, and an open leg is attributed
    -- back to its entry by strike, so the entry must carry its own.
    put_strike        NUMERIC(18,4),
    call_strike       NUMERIC(18,4),

    placed            BOOLEAN,
    partial           BOOLEAN,
    shadow            BOOLEAN,
    place_error       TEXT,
    broker_order_ids  TEXT,

    credit_actual     NUMERIC(18,2),
    placed_at_utc     TIMESTAMPTZ,

    exit_cost_actual  NUMERIC(18,2),
    close_trigger     TEXT,
    closed_at_utc     TIMESTAMPTZ,
    realised_pnl      NUMERIC(18,2),

    created_at_utc    TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (market, session, expiry_kind, entry_seq),

    -- A round-trip cannot end before it starts. The condition that produced
    -- -100.9m is now unrepresentable rather than merely reported.
    CONSTRAINT strangle_execution_closes_after_it_opens
        CHECK (closed_at_utc IS NULL OR placed_at_utc IS NULL
               OR closed_at_utc >= placed_at_utc)
);

-- Find the OPEN entry for a market fast; that is the hot lookup on every close
-- tick and on every P&L request.
CREATE INDEX IF NOT EXISTS strangle_execution_open_idx
    ON strangle_execution (market, session, expiry_kind)
    WHERE placed IS TRUE AND closed_at_utc IS NULL;

CREATE INDEX IF NOT EXISTS strangle_execution_session_idx
    ON strangle_execution (session DESC, market);

-- BACKFILL. Every decision row that recorded an execution becomes entry 1.
-- The known-incoherent pairs are carried across as-is EXCEPT that a closed_at
-- earlier than its placed_at is dropped to NULL: that timestamp belongs to a
-- different trade and copying it forward would launder the corruption into the
-- new table and trip the CHECK. What is lost is a value that was never true.
INSERT INTO strangle_execution
    (market, session, expiry_kind, entry_seq, put_strike, call_strike,
     placed, partial, shadow, place_error, broker_order_ids,
     credit_actual, placed_at_utc, exit_cost_actual, close_trigger,
     closed_at_utc, realised_pnl)
SELECT market,
       COALESCE(exchange_date, as_of)::date,
       COALESCE(expiry_kind, ''),
       1,
       put_strike, call_strike,
       placed, partial, shadow, place_error, broker_order_ids,
       credit_actual, placed_at_utc, exit_cost_actual, close_trigger,
       CASE WHEN closed_at_utc IS NOT NULL AND placed_at_utc IS NOT NULL
                 AND closed_at_utc < placed_at_utc
            THEN NULL ELSE closed_at_utc END,
       realised_pnl
  FROM strangle_decision_log
 WHERE placed IS NOT NULL
ON CONFLICT (market, session, expiry_kind, entry_seq) DO NOTHING;
