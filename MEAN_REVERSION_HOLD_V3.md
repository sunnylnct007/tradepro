# Swing hold period — 10 sessions to 20. Amendment, pre-registered.

**Committed BEFORE the forward test starts (2026-08-24).** The rule is being
changed the night before, and that requires a written justification, not a
quiet edit.

## The finding

Owner asked: *"what if I not hold for 10 sessions but let it fill the limit I
want to trade on like GTC."*

| exit rule | trades | win% | per trade | per day held | med hold | max hold | timeouts |
|---|---|---|---|---|---|---|---|
| **10 sessions (shipped)** | 2,312 | 64.6% | +0.71% | 0.1026% | 7 | 10 | **31.4%** |
| 20 sessions | 2,308 | **72.5%** | **+0.97%** | 0.1166% | 7 | 20 | 2.4% |
| 40 sessions | 2,306 | 72.6% | +0.98% | 0.1168% | 7 | 32 | 0.1% |
| GTC, no timeout | 2,303 | 72.7% | +0.99% | 0.1173% | 7 | 32 | 0% |

**The 10-session timeout was closing 31.4% of trades before they resolved** —
booking a result on positions where neither the target nor the stop had been
reached. It is the one exit that ends a trade which has not actually failed.

## Why 20 and not GTC

20 sessions captures essentially the whole gain (+0.97% of the +0.99%) while
keeping a hard bound on how long capital is committed. True GTC differs by
0.02%/trade and gives up the bound in exchange. Max observed hold is 32
sessions and only 3 trades in sixteen years never resolved at all, so the
bound is nearly free.

Median hold stays **7 sessions** at every setting — the timeout was never
affecting the typical trade, only truncating the tail.

## Evidence this is not curve-fitting

It survives the two-split test that rejected momentum v3, the intraday dip
study, and would have caught both on the full sample. The gain holds in
**all four cells**, and unusually evenly:

| cell | n | 10-sess | 20-sess | gain |
|---|---|---|---|---|
| time 1st half | 1,151 | +0.57% | +0.82% | **+0.25%** |
| time 2nd half | 1,157 | +0.84% | +1.12% | **+0.29%** |
| symbols even | 1,152 | +0.84% | +1.12% | **+0.28%** |
| symbols odd | 1,156 | +0.57% | +0.82% | **+0.25%** |

A curve-fit gain concentrates in one cell. This one does not vary by more than
four basis points across four independent cuts.

## Why change it the night before rather than after

The alternative is knowingly running the inferior rule for twelve weeks, which
spends the whole forward-test window measuring something we have already shown
is worse. The change is backtested on the same 2,300 trades, validated on the
same splits, and the test has not started.

**What this costs:** the forward test now validates a rule that has never been
paper-traded, rather than one that has also never been paper-traded. Nothing
is lost, because nothing had run yet.

## Revised live expectations

    entry at the next open, 20-session cap
    ~72% win · +0.97%/trade backtested · median hold 7 sessions · max 20

The live baseline stays roughly 0.09%/trade below the backtest for the
entry-timing delay, so expect approximately **+0.88%/trade**, not +0.97%.

`FORWARD_TEST_GATES_V1.md` gate F6 (>=15 completed trades) is unaffected — the
signal rate is unchanged at ~7/week; only the exits move.


---

# GRADED RESULT — all six gates pass, and the harness had a bug

Re-graded through `backtests/studies/mean_reversion_v2.py` after fixing a
defect the change itself exposed.

**The harness hardcoded its own `MAX_HOLD = 10`** instead of importing it, so
raising the constant in `signals/mean_reversion.py` never reached it. It kept
grading the old rule and appeared to CONTRADICT the result that motivated the
change — 64.6%/+0.80% against the comparison's 72.5%/+0.97%. That looked like
a real disagreement and was a duplicated constant. Same drift this session
already chased through `poison_check`, the strategy registry and the entry
rule. The harness now imports SIGMA, BB_WINDOW, STOP_PCT and MAX_HOLD.

Isolating one variable at a time first confirmed 20 beats 10 on EVERY
convention, so the direction never depended on the choice:

| entry | hold 10 | hold 20 |
|---|---|---|
| signal close | 64.6% / +0.80% | **72.8% / +1.06%** |
| next open, same-day exit allowed | 64.6% / +0.71% | **72.5% / +0.97%** |
| next open, same-day exit blocked | 65.0% / +0.76% | **72.9% / +1.03%** |

## Graded, 20-session hold

| gate | threshold | result | |
|---|---|---|---|
| V0 | >= 1,000 trades | 2,310 | PASS |
| G1 | win >= 55% | **72.8%** | PASS |
| G2 | mean net > 0 | **+1.06%** | PASS |
| G3 | median hold <= 10 | 7 | PASS |
| G4 | top-1% share <= 25% | **18.2%** | PASS |
| G5 | worst >= -25% | **-23.9%** | PASS |

Two-split passes in all four cells (70.0-75.6% win, +0.91% to +1.22%).

**G5 is the one that moved against us**: -17.7% to -23.9%. A longer hold means
more nights held, and more chances to gap through the stop. It still clears
the -25% gate but the margin is now 1.1 points, so G5 joins G4 as a gate to
watch if anything else changes.

**Revised live expectation: about +0.97%/trade**, being +1.06% less the
~0.09% entry-timing delay.
