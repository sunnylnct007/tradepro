-- EVERY strangle evaluation, not just the ones that traded.
--
-- Owner, 31 Aug 2026: "i need the stuff to be logged for analysis later on so
-- we might need a history table to store these evaluations and decisions", and
-- what it is for — "so we can evaluate what we did and why we did it and check
-- if it was right or not".
--
-- WHY A NEW TABLE rather than strategy_candidate_log. That one is per-SYMBOL
-- with a target and a stop, and it records CANDIDATES. A strangle decision is
-- per-MARKET, has two strikes and no target, and — critically — the
-- STAND-ASIDE rows are the most valuable rows in it. They are the only way to
-- answer "was the gate set right?", because they say what we refused and why.
-- A table of things we did cannot grade a strategy whose entire edge is what it
-- declines to do.
--
-- WHY IT MATTERS NOW. The Lambda writes its ledger to /tmp, which is wiped
-- between invocations, so every scheduled decision since the migration to
-- Lambda has been LOST. The forward test has been recording nothing.

CREATE TABLE IF NOT EXISTS strangle_decision_log (
    id              BIGSERIAL PRIMARY KEY,
    market          TEXT        NOT NULL,      -- SPY / NIFTY / BANKNIFTY ...
    as_of           DATE        NOT NULL,      -- the SETTLED session the gate read
    exchange_date   DATE,                      -- local session date at decision time
    decided_at_utc  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- WHAT was decided
    decision        TEXT        NOT NULL,      -- CANDIDATE | STAND_ASIDE | NO_DATA
    reason          TEXT        NOT NULL,      -- the sentence shown to a human

    -- WHY — every input the decision turned on, so it can be re-judged later
    -- without re-deriving anything or trusting that the rule never changed.
    vol_symbol      TEXT,
    vol_index       NUMERIC(10,4),
    vol_threshold   NUMERIC(10,4),
    iv_used_pct     NUMERIC(10,4),
    spot            NUMERIC(18,6),
    spot_basis      TEXT,                      -- session_open | prior_close
    provisional     BOOLEAN     NOT NULL DEFAULT FALSE,
    session_state   TEXT,                      -- pre_open | open | closed

    -- the structure that WOULD have been (or was) placed
    expiry_kind     TEXT,                      -- weekly | monthly
    dte             INT,
    put_strike      NUMERIC(18,6),
    call_strike     NUMERIC(18,6),
    forward         NUMERIC(18,6),
    lot             INT,

    -- money, as published
    collateral      NUMERIC(18,2),
    margin_estimate NUMERIC(18,2),
    credit_modelled NUMERIC(18,2),

    -- OUTCOME — filled in later by a grader, null until then. Nullable on
    -- purpose: a decision is recorded when it is MADE, and grading it before
    -- the session closes would be the same lookahead this strategy keeps
    -- having to be corrected for.
    index_close     NUMERIC(18,6),
    outcome_pct     NUMERIC(12,6),             -- % of collateral, same unit as the evidence
    outcome_note    TEXT,
    graded_at_utc   TIMESTAMPTZ,

    -- provenance: which image produced this row
    jobs_commit     TEXT,
    detail          JSONB
);

-- One row per market per session per expiry. A re-run (a UI trigger, a retry)
-- must UPDATE rather than append, or the history double-counts and every
-- summary over it is wrong.
CREATE UNIQUE INDEX IF NOT EXISTS strangle_decision_log_uniq
    ON strangle_decision_log (market, as_of, COALESCE(expiry_kind, ''));

CREATE INDEX IF NOT EXISTS strangle_decision_log_market_date
    ON strangle_decision_log (market, as_of DESC);

-- Ungraded rows are the work queue for the grader.
CREATE INDEX IF NOT EXISTS strangle_decision_log_ungraded
    ON strangle_decision_log (as_of DESC) WHERE graded_at_utc IS NULL;

COMMENT ON TABLE strangle_decision_log IS
    'Every index-strangle evaluation, INCLUDING stand-asides. The stand-aside rows are the point: they are the only evidence for whether the volatility gate is set correctly, because the edge of this strategy is what it declines to trade.';
COMMENT ON COLUMN strangle_decision_log.provisional IS
    'TRUE when the strikes were priced off the previous close because the session had not opened. Such a row records a DECISION but not a placeable trade — exclude when grading fills.';
