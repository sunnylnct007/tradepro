-- APPEND-ONLY record of every candidate a screen published, on the day it did.
--
-- Owner, 29 Aug 2026: paper-trade the post-earnings puts from Monday. There is
-- no option-order path yet (OPTION_EXECUTION_SCOPE.md), and building one over a
-- weekend is how a paper account fills with fills nobody can reconcile. But the
-- forward-test EVIDENCE does not need orders — it needs the candidate captured
-- with its prices, dated, and never overwritten.
--
-- WHY A NEW TABLE. today_setups_results is PRIMARY KEY (universe, label) with
-- label defaulting to 'latest', so every run REPLACES the previous one. It is
-- the right shape for "what does the screen show now" and the wrong shape for
-- "what did it show on each of the last 60 days". Nothing accumulated; a
-- forward test on it would have had one row.
--
-- One row per (strategy, symbol, signal_date). Re-running the same day is
-- idempotent — the screen runs several times a session and must not multiply
-- rows — but a LATER run of the same day updates the prices, because the last
-- run before the close is the most accurate view of that session.
--
-- This is deliberately NOT an orders table. It records what was PUBLISHED, not
-- what was traded, so the two can be compared later: did the fill land where
-- the screen said it would (forward-test gate F1).

CREATE TABLE IF NOT EXISTS strategy_candidate_log (
    strategy      TEXT        NOT NULL,
    symbol        TEXT        NOT NULL,
    signal_date   DATE        NOT NULL,
    published_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The numbers a later grader needs. Nullable because a screen may not
    -- produce all of them, and NULL must stay distinguishable from zero --
    -- the distinction that let zero-price fills hide for two months.
    spot          NUMERIC(18,6),
    strike        NUMERIC(18,6),
    target_price  NUMERIC(18,6),
    stop_price    NUMERIC(18,6),
    dte           INT,
    annual_vol_pct NUMERIC(10,4),
    size_factor   NUMERIC(10,4),
    collateral    NUMERIC(18,2),
    -- Everything else the screen said, verbatim, so a field added later is not
    -- lost from the historical record before its column exists.
    detail        JSONB,
    PRIMARY KEY (strategy, symbol, signal_date)
);

COMMENT ON TABLE strategy_candidate_log IS
  'Append-only: what each screen PUBLISHED, per day. Not orders. Feeds forward-test grading — did the fill land where the screen said.';

CREATE INDEX IF NOT EXISTS idx_candidate_log_strategy_date
    ON strategy_candidate_log(strategy, signal_date DESC);
