# INTRADAY VWAP/ORB GATES V1 — grading a proposed intraday sleeve

28 Sep 2026. An outside proposal arrived as working code with its own gates
already written, which is the right way round and rare. The gates below are
**the proposal's own**, not ours — nothing was moved to fit the answer.

## What the proposal got right, recorded before the result

- **The fill model is pessimistic where it counts.** When one bar contains both
  the stop and the target it assumes the STOP. That single choice separates an
  honest intraday backtest from a fantasy one.
- **No look-ahead.** VWAP is accumulated bar by bar; at bar *i* it uses 0..*i*.
  Checked, not assumed.
- **Walk-forward** picks parameters on the first 60% and scores on the rest.
- **`max_variants_before_oos: 6`** is a gate on HOW MUCH SEARCHING WAS DONE,
  not on the result. Almost nobody writes that down.

## What was added, and why the result is not the proposal's own claim

**COSTS.** The proposal models none — no spread, no commission, no slippage.
Its reversion target is 1.0% gross against a 0.8% stop, a structural payoff of
~1.25 against a gate demanding ≥1.20. There is no room. A costless result
cannot be graded against gates that assume real trading, so **0.04% round trip**
was added — deliberately modest for liquid US large caps, and the run reports
the zero-cost figure alongside so the sensitivity is visible.

**A SESSION FLOOR.** The gates need 60 out-of-sample trades. At ≤2 trades a day
with a 40% holdout that needs ~75 sessions per symbol, and **941 of our 973
symbols hold under 50**. Only 29 have ≥250. Grading the thin ones would measure
the sample, not the rule.

## RESULT — run 28 Sep 2026. FAILS 3 of 5 gates out of sample.

29 symbols, 14,917 pooled sessions, 8,950 in-sample / 5,967 out-of-sample.
Chosen in-sample: stretch 0.007, target 0.002.

| | n | hit rate | avg/trade | payoff | expectancy |
|---|---|---|---|---|---|
| **OOS all** | 4,349 | 54.0% | +0.036% | 0.91 | **+$8.89** |
| OOS reversion | 545 | 53.8% | +0.068% | 1.07 | +$16.89 |
| OOS orb | 3,804 | 54.0% | +0.031% | 0.90 | +$7.74 |

    trades >= 60        PASS
    hit >= 55%          FAIL  (54.0%)
    payoff >= 1.20      FAIL  (0.91)
    expectancy >= $40   FAIL  ($8.89 against a $40 bar)
    variants <= 6       PASS

### The method held even though the strategy did not
In-sample expectancy $8.04, out-of-sample $8.89. **The out-of-sample result is
slightly BETTER than in-sample**, which is what a non-overfit study looks like.
The walk-forward, the variant cap and the pessimistic fill all did their job.
The rule simply does not clear its own bar.

### Costs are half the edge
Zero-cost expectancy is **$18.89**; net of 0.04% it is **$8.89**. A modest,
realistic round trip removes 53% of the gross edge. That is the number the
proposal could not see, and it is the difference between "thin but interesting"
and "not worth the operational load".

### Reversion is twice the trade ORB is, and fires a seventh as often
Per trade, reversion is +0.068% against ORB's +0.031%, with the only payoff
above 1.0 in the study. But ORB produces **3,804 of the 4,349 trades** and drags
the pooled average down to something no gate would accept.

The proposal's `classify()` sends each session to one setup or the other, so the
two are never compared on the same sessions — the pooled figure is really "ORB,
diluted by a little reversion". Anyone reading only the `all` row would conclude
the whole idea is flat, when one half of it is meaningfully better than the
other.

### Consequence — DO NOT BUILD, and the useful half is named
Per the proposal's own gates: three failures, not marginal. +$8.89 per trade on
$25,000 does not pay for an intraday sleeve's operational load, and 54% with a
0.91 payoff is a coin flip that loses slightly more than it wins.

**What is worth keeping** is the observation that the VWAP-reversion half
carries a real, if thin, edge (+0.068%/trade, payoff 1.07, 545 out-of-sample
trades) while the opening-range half does not (payoff 0.90). A study of
reversion ALONE, on volatile names rather than the liquid large caps we happen
to have depth on, is a different question this one cannot answer — see below.

### What we could NOT test, and it is the part the owner actually wants
The owner's intraday interest is **volatile names** — the SNDK short they
traded profitably, and MU. We have:

    SPY   771 sessions      MU    39 sessions
    AAPL  530 sessions      SNDK  34 sessions

The 29 gradeable names are liquid large caps because that is where the 5m
history happens to be deep. **This result says nothing about the trade the owner
is actually interested in.** IBKR serves ~2 years at 5m, so MU and SNDK are
backfillable; until they are, the honest position is that the volatile-name
version is untested rather than unpromising.
