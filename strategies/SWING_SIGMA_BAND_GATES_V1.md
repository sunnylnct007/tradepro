# Swing σ-band study — PRE-REGISTERED gates, v1

**Committed BEFORE the run**, 8 Sep 2026. Owner, looking at COST at −2.27σ:
*"entry need below −2.5 shouldn't be a hard limit provided we can prove with
analytics."* Agreed — 2.5 was the rule as pre-registered, but the cut itself
was never separately proven against its neighbours.

## The design that avoids data-mining

NOT "sweep σ and pick the best" — that is curve fitting with extra steps.
The question is MARGINAL: do the trades between −2.0σ and −2.5σ — the ones
the rule currently refuses — carry their own weight, graded as their own
population with the SAME exits (20-day-mean target, −8% stop with fill-at-
open slippage, 20-session cap) and the SAME trend floor?

Bands, each graded independently:
  A: σ ≤ −2.5            (the rule — reproduction control, must match)
  B: −2.5 < σ ≤ −2.25    (the near-misses like COST)
  C: −2.25 < σ ≤ −2.0

## Gates — a band earns entry into the rule ONLY if ALL pass

| # | Test | Threshold |
|---|------|-----------|
| S0 | Trades in the band | ≥ 300 |
| S1 | Win rate | ≥ 65% |
| S2 | Mean net return per trade | ≥ +0.75% |
| S3 | Two-split (time × symbol) | mean positive in ALL FOUR cells |
| S4 | Worst single trade | ≥ −25% |

The bars sit just under the rule's own record (72.8% / +1.06%) because a
shallower dip is a weaker signal by construction; matching the rule outright
is not required, being independently good is.

## Prediction — recorded before the run

Band B (−2.25..−2.5) passes S0/S4, and **fails S3 in at least one cell** —
shallower dips lean harder on regime. Band C fails S1 or S2 outright.
~30% that any band earns in. If B passes cleanly, the entry widens to −2.25
via a gates amendment and the screen's near-misses become candidates — which
is exactly what the owner suspects. Thresholds do not move after the numbers.

## RESULT — run 8 Sep 2026, thresholds untouched

| band | n | win | mean | worst | cells | verdict |
|---|---|---|---|---|---|---|
| A ≤−2.5 (control) | 1989 | 71.7% | +1.01% | −17.7% | all + | reproduces the record |
| B −2.5..−2.25 | 1660 | 70.4% | +0.87% | −19.6% | +0.91/+0.82/+1.25/+0.50 | **PASSES ALL FIVE → entry widens to −2.25** |
| C −2.25..−2.0 | 2582 | 72.3% | +0.89% | −27.9% | all + | FAILS S4 (tail) — stays out |

Prediction was wrong (expected B to drop a cell; ~30% any band earns in).
Amendment executed: `SIGMA = 2.25` in signals/mean_reversion.py. Live trade
records split at this date. Harness: backtests/studies/sigma_band_v1.py.
