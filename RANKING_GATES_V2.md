# A ranking rule that survives a WIDER pool. Pre-registered.

**Written BEFORE the run, 25 Aug 2026.** Committed so the winner cannot be
chosen after seeing results.

## The problem this has to solve

`ranking_v1` adopted **reward:risk** — upside to the 20-day target over the
fixed 8% stop — after it beat five rivals and was the only one positive in all
four two-split cells.

`universe_width_v1` then showed it breaks when the pool widens. On 991 names
instead of 244:

* extra names score HIGHER on reward:risk (0.83 vs 0.75)
* they take 27% of slots
* and deliver +0.63% against the universe names' +0.67%
* net effect: cap-12 mean falls +0.76% → +0.66%, worst trade −17.7% → −21.6%

**The mechanism: reward:risk's numerator grows with volatility while its
denominator is a fixed 8%.** A more volatile name sits further from its 20-day
mean when it dips, so it scores higher without being a better trade.

This is the mirror of why deepest-sigma lost. Sigma divides by the symbol's own
volatility, which over-corrects because the stop is absolute. Reward:risk
divides by nothing, which under-corrects on a mixed pool. **The answer, if
there is one, is somewhere between "normalise fully" and "not at all" — and
that is a suspicious place to go looking, because it is exactly where a tunable
parameter would hide.** The gates below are built to catch that.

## Candidates

All computable from the signal bar. `up` = target/close − 1. `atr` = 14-day ATR
as a percent of close.

| # | rule | reasoning |
|---|---|---|
| N0 | `up / 0.08` | **the control** — the incumbent, reward:risk |
| N1 | `up / atr` | fully ATR-normalised: how many average sessions of movement to the target. ATR includes gaps, which the close-to-close sigma does not. |
| N2 | `up / sqrt(atr)` | square-root damping — half-normalised, the explicit middle |
| N3 | `up / 0.08 − 0.1 * atr` | reward:risk with a linear volatility penalty |
| N4 | `-atr` among firing names | ignore upside, just prefer the calm name |
| N5 | `up / 0.08`, restricted to names with atr below the pool median | keep the incumbent, fix the POOL instead of the rank |

N5 is included deliberately: if the problem is a heterogeneous pool, the honest
fix may be to re-homogenise the pool rather than invent a cleverer score.

## Gates — all five required

| gate | threshold |
|---|---|
| **W1** | beats N0 on the WIDE pool (991) at cap 12 — this is the point |
| **W2** | does NOT lose to N0 on the NARROW pool (244) by more than 0.03%/trade — must not break what works |
| **W3** | survives the TIME split on the wide pool — both halves |
| **W4** | survives the SYMBOL split on the wide pool — both cells |
| **W5** | worst trade on the wide pool no worse than N0's −21.6% |

## Predictions, written before the run

I expect **N1 (up/atr)** to win on W1, because ATR is the volatility measure
that includes gaps and the stop is what gaps kill. I am genuinely unsure
whether it clears W2 — full normalisation is what lost last time, and on the
narrow pool it may reproduce the sigma failure.

I expect **N2 to be the most dangerous result**: a half-normalisation with a
free exponent is where a curve fit would live, and if N2 wins by a nose over N1
and N0 I should treat that as evidence of tuning, not of insight.

I expect **N4 to fail** — ignoring upside entirely threw away the only
information that made ranking work at all.

**N5 is the one I would most like to win**, because it needs no new score and
it says something structural: that the universe's job is homogeneity. If N5
matches N1, I will prefer N5 on the grounds that it adds no parameter.

## If nothing passes

Then the honest conclusion is: **reward:risk is correct on a homogeneous pool
and the universe must stay narrow.** Expansion would then be blocked on the
liquidity gate (which needs the volume repair), not on ranking. That is a
legitimate result and will be reported as one rather than softened into a
marginal winner.
