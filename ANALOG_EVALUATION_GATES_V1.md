# Analog evaluation v1 — does knowing the CURRENT STATE beat the base rate?

**Status: PRE-REGISTERED. Committed BEFORE the run.**

## The question

Owner: *"what about evaluating a particular candidate at a given point in
time ... we have so much data we should be able to evaluate and backtest
already."*

Right now every screen quotes a UNIVERSE average at an individual row. The
momentum screen says "47% win" whether the row is HPQ or MDB. The drill-down
improved on that by showing the rule's record on that specific symbol, but
that is still a symbol-level average — it says nothing about whether the
symbol is in a *similar situation* now to when it worked.

So: take the state a candidate is in today — trend, extension, volatility,
distance from its high — find the historical moments that looked like it, and
report what happened next.

**The hypothesis is that this beats simply quoting the base rate.** That is
the whole claim, and it is the only thing these gates test.

## Why this needs gates rather than a screen

An analog panel is the most persuasive-looking thing we could build. It will
always produce a number, that number will always have a story attached, and
it will look like insight whether or not it carries any. Momentum v3 was
rejected for exactly this shape of error — a table that looked monotonic and
was a regime.

If conditioning does not beat the unconditional base rate, the honest product
is the base rate, and this file joins the other failures.

## Method

State vector at bar i, all scale-free so a $12 stock and a $960 stock are
comparable:

    pct vs 200-SMA · pct vs 50-SMA · pct vs 20-SMA · ATR% (14)
    pct below 52-week high · 20-session return

Each dimension is z-scored **using only data available at bar i**. Analogs are
the K=50 nearest neighbours by Euclidean distance in that space.

**NO LOOKAHEAD, enforced structurally**: analogs for bar i are drawn only from
bars strictly earlier than `i - horizon`, so no analog's own outcome window
can overlap the moment being evaluated. This is asserted in code, not assumed.

Outcome predicted: P(+8% before -8% within 20 sessions), the momentum sleeve's
own shape.

Comparison: for the same bar, the **base rate** prediction is the realized
frequency over all prior bars — the number a screen would quote today.

## Gates

Analog evaluation ships only if it passes **every** gate.

| # | Gate | Threshold | Why |
|---|---|---|---|
| G1 | Brier score vs base rate | analog Brier < base Brier by >= 0.005 | Must be better calibrated, not merely different. A tiny margin is noise. |
| G2 | Decile spread | top-decile realized rate - bottom-decile >= 10pp | If the score cannot separate good moments from bad, it is decoration. |
| G3 | Monotonicity | realized rate rises across >= 7 of 10 deciles | A spread with no ordering in between is one lucky bucket. |
| G4 | **Survives both splits** | G1 and G2 hold in all 4 cells | Time split AND symbol split — the rule that caught momentum v3. |
| G5 | Sample | >= 20,000 evaluated bars, >= 100 symbols | |
| G6 | No lookahead | assertion passes on every evaluation | Structural, not statistical. A single violation invalidates the run. |

## Prediction, on record, before running

1. **G1 passes narrowly.** State clearly carries *something* — trend filters
   work — so I expect the analog Brier to beat base rate, but by a small
   margin: 0.005-0.015.
2. **G2 is the coin flip.** I predict a decile spread of **8-14pp**, i.e.
   straddling the threshold. This is the gate most likely to decide it.
3. **G4 survives, unlike v3.** Trend/volatility state is not a 2020-specific
   phenomenon the way volume turned out to be, so I expect this to hold in
   both halves. If it does NOT, that is a strong signal the whole
   state-conditioning idea is regime-bound.
4. Overall: **~50%** that this ships. Genuinely uncertain, which is the point
   of writing it down first.

If it fails, the screens keep quoting the base rate and say so plainly.
