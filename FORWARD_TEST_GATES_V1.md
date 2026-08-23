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

**REVISED 23 Aug** after IBKR volume was found to be stored in 100-share lots,
which had wrongly excluded 155 liquid names as too thin to trade. On the
corrected **244-name** universe the rule fires **7.0 times per week** (366
signals in the last 12 months), not 2.9.

Four weeks is therefore **about 28 trades**, not 12. The monthly spread is
still severe: 88 in March 2026, 8 so far in August.

**This changes the recommended window.** 70-80 trades — the point at which a
65% win rate can be told from a coin flip — now arrives in **10-11 weeks,
about 2.5 months**, not the six months computed on the broken universe.

**At n≈28 the edge still CANNOT be validated.** A 65% win rate over 28 trades
has a 95% interval of roughly 46%-80% — it still contains 50%, so four weeks
cannot show it beats a coin flip. Any outcome in that range — including a
miserable one — is consistent with the backtest being exactly right. A four-week
test that "confirms" the edge would be measuring noise, and one that
"disproves" it would be doing the same.

To distinguish a true 65% from a coin flip with reasonable power needs roughly
**70-80 trades ≈ 10-11 weeks** at the corrected rate.

**RECOMMENDED: run 12 weeks, not 4.** The execution gates below are answerable
within the first fortnight; carrying on to twelve weeks costs nothing extra
and converts a plumbing test into an edge test. Grading the execution gates
early and the edge gates at the end gets both from one window.

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
| F6 | Trade count | >= 15 completed trades | Below this even the execution questions cannot be answered. If unmet, EXTEND the window — do not grade it. |

**Explicitly NOT a gate: P&L, win rate, or expectancy.** They are recorded and
reported, never graded, at this sample size. Recording them without grading
them is deliberate: it builds the sample toward the six-month test.

## Failure means

F1, F2 or F5 failing = a platform defect. Fix it, restart the window.
F3 or F4 failing = the backtest's assumptions are wrong; the gates file is
re-opened before any further forward testing.

## Data freeze — agreed with the DATA lane, on THEIR revised terms

The original proposal was "nothing changes for the window". The data lane
withdrew that as not credible and I agree with their reasoning: in two days
they found wrong-contract poison, a routing default that refiled quarantined
symbols, a 100x dividend error, two 52-week conventions on one screen, an
RVOL that was wrong by construction, and volume stored in 100-share lots.
Promising no changes for three months means either breaking the promise or
knowingly serving data known to be wrong — **and a known-wrong dataset is
worse for this test than a logged change.**

The rule actually in force is **"nothing changes SILENTLY"**:

* **No discretionary changes** — no convention changes, no universe edits, no
  re-sourcing sweeps, no refactors of stored data.
* **Corrections ARE allowed** when correctness demands, and every one is
  logged in `DATA_CHANGE_LOG.md` with date, what changed, symbols affected,
  date range affected and commit hash.
* **Routine harvesting is not a change** — that is the test running.

**Any anomaly in the window is checked against `DATA_CHANGE_LOG.md` FIRST**,
before it is attributed to the strategy. That is the whole point: the freeze
does not prevent data from moving, it prevents data moving *unaccountably*.

**Freeze window: from the start date below, TWELVE weeks** (revised from four — the corrected signal rate makes an edge answer reachable, and a freeze that ends before the test does is not a freeze).

## Start date

    START:  2026-08-24  (Monday — the first trading session after the owner
                         said "start now" on Sunday 23 Aug)
    END:    2026-11-16  (START + 84 days)

    Broker:   IBKR paper (account DUP656969)
    Strategy: mean_reversion_swing
    ICH continues on Trading 212 and is NOT part of this test.
    The ICH IBKR clone was stopped 22 Aug and its slot is now Swing's.

## Live baseline — what "as expected" looks like

The backtest enters at the signal-bar CLOSE, which no order can achieve. Entry
at the NEXT OPEN was measured separately:

    signal close (backtest)     64.9% win   +0.854%/trade
    next open (achievable)      64.9% win   +0.769%/trade

**+0.77%/trade is the live baseline.** The 0.085% difference is the cost of the
delay, not slippage, and F3 measures only what happens beyond it. The overnight
gap at entry runs +0.13% median — a dip buyer is buying after a fall, so the
open tends to gap UP against you.
