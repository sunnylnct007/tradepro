-- What DELTA-mode strike selection WOULD have chosen, recorded beside what we
-- actually traded.
--
-- Spec v1.0 §2.1 (8 Sep 2026): solve each strike for a target delta rather than
-- placing them equidistant. It is the only formulation that is delta-neutral BY
-- CONSTRUCTION — short a call at -δ* and a put at +δ* nets to zero.
--
-- §4 predicts equidistant strikes run "net long delta by 3-8 deltas per lot".
-- We measured SPX at +0.126 on 7 Sep — 12.6 deltas, WORSE than the estimate.
-- The owner's manual trades have sold the call 200-500 points closer than the
-- system on every one of five sessions, which is delta-matching by feel, and
-- have beaten it.
--
-- NOTHING CHANGES SELECTION. The published 82.9% win rate describes the
-- EQUIDISTANT rule; swapping it would invalidate every figure this strategy
-- reports — the same trap as the iron-condor substitution. Both pairs are
-- recorded every day, and the switch becomes an evidence-based decision rather
-- than an argument about skew.

ALTER TABLE strangle_decision_log
    ADD COLUMN IF NOT EXISTS delta_put_strike   NUMERIC(18,6),
    ADD COLUMN IF NOT EXISTS delta_call_strike  NUMERIC(18,6),
    -- what the solved pair's balance actually is, to compare against net_delta
    ADD COLUMN IF NOT EXISTS delta_mode_net     NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS delta_target       NUMERIC(10,4),
    -- §2.1 guardrail: outside [Δ_min, Δ_max] the spec says SKIP THE DAY. A vol
    -- crush leaves you selling near-ATM for scraps; a spike blows the wings
    -- into illiquidity. Recorded so a skip can be judged, not guessed at.
    ADD COLUMN IF NOT EXISTS delta_in_band      BOOLEAN;
