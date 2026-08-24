# Swing forward test — graduation rule, pre-registered

**Status: WRITTEN BEFORE THE TEST. Start date NOT set — that is the owner's
call.** Everything below is fixed in advance so the result cannot be argued
into a pass afterwards.

## What is being tested

Swing (mean reversion) on ONE broker, paper, no real money. Evidence behind it:
`MEAN_REVERSION_GATES_V1.md` + the v2 re-run — **2,310 trades, 72.8% win,
+1.06%/trade backtested, worst -23.9%**, harness at
`backtests/studies/mean_reversion_v2.py`.

**Live baseline: +0.97%/trade**, being +1.06% less the 0.09% cost of entering
at the next open rather than the signal close. Do not deduct that delay twice
— see the note in `signals/mean_reversion.py`.

(This paragraph read "1,270 trades, 66.2% win, +0.88%/trade, worst -17.7%"
until 24 Aug. Those were the 89-name universe, before the IBKR 100-share-lot
volume correction restored 155 liquid names, and before the hold moved from 10
to 20 sessions. Corrected here rather than quietly, because these are the
figures the test is graded against.)

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
| F3 | Entry slippage | median <= 0.30% vs the RECORDED reference price | The backtest assumed the signal-bar close. Worse than this and the edge is eaten at the door. **See the amendment below — this gate was UNGRADEABLE as written.** |
| F4 | Stop behaviour | every stop-out fills at or below `min(stop, open)` as modelled | The v2 harness models gap-through. If reality is worse, the -17.7% worst trade is optimistic. |
| F5 | No silent failures | zero sessions where the screen fails to run without a loud alert | A screen that quietly does not fire is indistinguishable from a screen with no signals. |
| F6 | Trade count | >= 15 completed trades | Below this even the execution questions cannot be answered. If unmet, EXTEND the window — do not grade it. |

**Explicitly NOT a gate: P&L, win rate, or expectancy.** They are recorded and
reported, never graded, at this sample size. Recording them without grading
them is deliberate: it builds the sample toward the six-month test.

## AMENDMENT, 23 Aug — F3 was ungradeable and is now fixed

Written before the window opened, after checking the gate against real data
rather than assuming it would work.

**F3 measures entry slippage "against the published price", and nothing
recorded what that price was.** The OMS stores no reference for a market order
— `limitPrice` is null by definition — so there was nothing to measure
against.

The check that found it: 4 months of live paper fills, matched to their
sessions. **All 43 matched fills sit INSIDE their session's high-low range**,
so they are genuine fills. Comparing them to the session OPEN gave a median
"slippage" of +0.335% with outliers past 5%, and 51% of fills breaching the
0.30% gate — but that number is measuring WHEN an order filled during the day,
not how badly it filled. Timing, not slippage.

Had this gone unfixed, F3 would have failed on day one for reasons unrelated
to Swing, or been quietly reinterpreted in week twelve. That is the same shape
of failure as G4 in the rejected intraday-dip study — a gate that cannot be
computed, discovered after the run.

**Fix:** Swing's entry orders now carry the signal-bar close as
`risk_target_price` / `risk_stop_price` and in the order tag (`ref=`). F3 is
measured against that recorded reference, not against a session open.

**The 0.30% threshold itself remains UNVALIDATED** and is retained only because
there is no evidence to replace it with. The live record cannot calibrate it,
for the reason above. First real slippage numbers arrive in week one, and if
they show 0.30% was the wrong number that is an honest amendment to make then
— with data — rather than a failed gate in week twelve.

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

## What a NORMAL result looks like — simulated before the test, on purpose

`backtests/studies/forward_simulation.py`. 20,000 bootstrap runs over the real
2,453-trade record, ~84 trades in 12 weeks, 5% of capital each.

| | 5th pct | median | 95th pct | P(loses money) | worst drawdown |
|---|---|---|---|---|---|
| losses independent | -0.1% | +4.0% | +8.3% | 5% | -3.0% |
| **losses CLUSTER (realistic)** | **-5.0%** | **+4.2%** | **+12.6%** | **21%** | **-8.3%** |

**There is a ONE IN FIVE chance this window loses money while the strategy is
working exactly as measured.** Losses cluster — bad conditions persist for
months, which is what 2022 was — so the independent-losses figure understates
the risk of a losing quarter by a factor of four. Both are shown for that
reason and the clustered one is the one to plan against.

Recorded BEFORE the window opens so neither of us can reinterpret the outcome
afterwards. Concretely:

* **A twelve-week loss is not evidence the strategy is broken.** It is inside
  the 5th-to-95th range and happens one time in five.
* **A +10% quarter is not evidence it is better than measured** either — that
  is the 90th percentile of the same distribution.
* **A drawdown of 8% intra-window is normal.** Only below roughly -12%, or a
  result outside this band entirely, is there anything to explain.

This is why P&L is RECORDED BUT NOT GRADED in this window. The execution gates
are answerable at 84 trades; the edge is not.

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
