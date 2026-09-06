-- Is the strangle actually delta-neutral, or only equidistant?
--
-- Strikes are placed at +/-1.5x the expected move: EQUIDISTANT IN PERCENT.
-- Index options carry volatility skew, so an equidistant put is fatter than
-- the call on both premium AND delta. The pair then sits NET LONG delta, which
-- a short strangle is not supposed to be — and a falling market hurts more
-- than the design intends. SPX on 1 Sep 2026: put -1,625, call +686.
--
-- The owner's manual trades sell the call 200-500 points closer than the
-- system on every one of four sessions. That is delta-matching by feel, and it
-- beat the system three times out of four.
--
-- These columns turn the argument into a measurement. NOTHING changes strike
-- selection: the published 82.9% win rate describes the EQUIDISTANT rule, and
-- swapping the selection would invalidate it exactly as the iron-condor
-- substitution would have. Measure first, then decide with evidence.

ALTER TABLE strangle_decision_log
    ADD COLUMN IF NOT EXISTS put_delta   NUMERIC(10,4),
    ADD COLUMN IF NOT EXISTS call_delta  NUMERIC(10,4),
    -- Short a put is +delta, short a call is -delta. Zero is neutral;
    -- positive means the position is LONG the market.
    ADD COLUMN IF NOT EXISTS net_delta   NUMERIC(10,4);
