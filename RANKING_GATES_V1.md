# Which signal do you take when there aren't enough slots? Pre-registered.

**Written BEFORE the run, 25 Aug 2026.** Committed so the winner cannot be
chosen after seeing the results.

## Why this exists

`portfolio_capacity_v1` established that Swing's per-trade quality falls from
**+1.10% to +0.52%** when concurrency is capped at 8 and signals are taken
first-come-first-served. Over half the edge is lost to *which* signals get
skipped — so the selection rule is not a refinement, it is a load-bearing part
of the strategy.

Today nothing ranks. The live strategy takes signals in symbol-loop order,
which is alphabetical. AAPL beats ZBRA for no reason at all.

## What is being tested

At a fixed cap, when more signals fire than there are free slots, rank the
COMPETING signals and take the best. Only same-day competition is ranked —
that is the actual decision the system faces.

Candidates, each of which must be computable from the signal bar alone:

| # | rule | why it might work |
|---|---|---|
| R0 | alphabetical | **the control.** What we do now. |
| R1 | deepest sigma below the mean | the rule is mean reversion; further from the mean should mean more to revert |
| R2 | best reward:risk | distance to the 20-day mean vs distance to the -8% stop |
| R3 | lowest ATR% | a quieter name is less likely to be stopped by noise |
| R4 | furthest above the 200-day average | strongest trend behind the dip |
| R5 | best own historical mean, shrunk | the per-symbol scorecard already on the Scanner |

## Gates

A ranking rule is ADOPTED only if it clears all four:

| gate | threshold |
|---|---|
| **K1** | beats the alphabetical control on per-trade mean at cap 8 AND cap 15 |
| **K2** | the improvement survives a TIME split — both halves |
| **K3** | the improvement survives a SYMBOL split — both odd and even |
| **K4** | it does not make the worst trade worse by more than 2 points |

K2 and K3 are the two-split test that rejected momentum v3, the intraday dip
study, and both resting-limit studies. A ranking rule that only works in one
half of the data is a curve fit, and the whole point of ranking is that it
will be applied to signals we have not seen.

**If NO rule clears the gates, the answer is that ranking cannot be improved
on this evidence, and the cap should be set high enough that ranking rarely
binds.** That is a legitimate outcome and it will be reported as one.

## Prediction, written before the run

I expect **R1 (deepest sigma)** to win, because it is the only candidate that
is a direct expression of the rule's own thesis — this is a mean-reversion
strategy and R1 says "take the one furthest from its mean".

I expect **R5 (own historical mean) to FAIL the symbol split**, because it is
fitted per-symbol by construction and that is exactly what a symbol split is
designed to catch.

I am least sure about **R3 (lowest ATR)**. It is the only defensive candidate,
and the -8% stop is fixed in percentage terms while ATR is not — so on a
quieter name the stop sits further away in noise-units. That is a real
mechanism, not a story, which makes it the one I would be least surprised to
be wrong about.

**What would make me distrust a win:** any rule that beats the control by a
large margin on the full sample but by little in one of the four split cells.
Size of win is not evidence; consistency across cells is.
