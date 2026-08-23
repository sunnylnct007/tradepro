# Swing forward test — graduation rule, pre-registered

**Status: WRITTEN BEFORE THE TEST. Start date NOT set — that is the owner's
call.** Everything below is fixed in advance so the result cannot be argued
into a pass afterwards.

## What is being tested

Swing (mean reversion) on ONE broker, paper, no real money. Evidence behind it:
`MEAN_REVERSION_GATES_V1.md` + the v2 re-run — 1,270 trades, 66.2% win,
+0.88%/trade, worst -17.7%, harness at `backtests/studies/mean_reversion_v2.py`.

Nothing in this platform has ever been forward-tested. That is the gap, not
more backtests.

## ⚠️ WHAT FOUR WEEKS CAN AND CANNOT ANSWER

**Measured, not assumed:** the rule fires **2.9 times per week** across the
89-name universe (151 signals in the last 12 months). Four weeks is therefore
**about 12 trades** — and the monthly spread is severe: 42 in March 2026, 1 so
far in August.

**At n≈12 the edge CANNOT be validated.** A 66% win rate over 12 trades has a
95% interval of roughly 39%-93%. Any outcome in that range — including a
miserable one — is consistent with the backtest being exactly right. A four-week
test that "confirms" the edge would be measuring noise, and one that
"disproves" it would be doing the same.

To distinguish a true 66% from a coin flip with reasonable power needs roughly
**70-80 trades ≈ six months** at the observed rate.

**So this test is about EXECUTION, not edge.** It answers: do signals fire when
they should, do orders reach the broker, do fills land where the screen said,
and does every fill reconcile to a signal. Those are answerable at n=12 and
they are exactly what has never been checked.

Anyone reading a four-week P&L as a verdict on the strategy has misunderstood
the test. That is stated here so it cannot be claimed later.

## Graduation gates

Assessed at the end of the window. **All must pass to proceed to a longer
edge test.**

| # | Gate | Threshold | Why |
|---|---|---|---|
| F1 | Signal fidelity | >= 95% of live candidates match what the committed harness produces for the same dates | Tests the PIPELINE. A live screen disagreeing with its own backtest invalidates everything downstream. |
| F2 | Every fill reconciles | 100% of fills trace to a published signal | An unattributable fill means something else is trading. Zero tolerance. |
| F3 | Entry slippage | median <= 0.30% vs the published entry | The backtest assumed the signal-bar close. Worse than this and the edge is being eaten at the door. |
| F4 | Stop behaviour | every stop-out fills at or below `min(stop, open)` as modelled | The v2 harness models gap-through. If reality is worse, the -17.7% worst trade is optimistic. |
| F5 | No silent failures | zero sessions where the screen fails to run without a loud alert | A screen that quietly does not fire is indistinguishable from a screen with no signals. |
| F6 | Trade count | >= 5 completed trades | Below this even the execution questions cannot be answered. If unmet, EXTEND the window — do not grade it. |

**Explicitly NOT a gate: P&L, win rate, or expectancy.** They are recorded and
reported, never graded, at this sample size. Recording them without grading
them is deliberate: it builds the sample toward the six-month test.

## Failure means

F1, F2 or F5 failing = a platform defect. Fix it, restart the window.
F3 or F4 failing = the backtest's assumptions are wrong; the gates file is
re-opened before any further forward testing.

## Data freeze — agreed with the DATA lane

For the duration of the window, on the data side: **no store-wide remediation,
no convention changes, no universe edits without recording them.** Routine
harvesting continues — that is the test running, not a change to it.

Reason: on 22 Aug alone the data lane purged wrong-contract series, retired a
tree, redefined routing, changed the 52-week convention and corrected RVOL.
Every one was correct. Every one moved numbers underneath somebody. If that
continues mid-test, a bad week becomes unattributable — strategy
underperformance and a data change are indistinguishable after the fact.

Anything that must break the freeze gets a dated line in `SHARED_CONTEXT.md`
naming the symbols and dates affected, so any anomaly can be checked against
it.

**Freeze window: from the start date below, four weeks.**

## Start date

    START:  __________  (not set — owner's decision)
    END:    START + 28 days

The window is not running until this is filled in and committed.
