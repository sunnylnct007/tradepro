# Momentum v2 — PRE-REGISTERED gates

**Committed BEFORE the v2 run** (22 Aug 2026). New file for a new test, per the
standing rule; `MOMENTUM_GATES_V1.md` is immutable and stays as the record.

## What v1 established, and the ONE gate being changed

v1 (8c233e6) ran 12 combinations. No variant passed all six. But the failures
were informative rather than fatal, and the pattern was unusually clean:

    exit            win%    mean%    hold
    close<10SMA     ~40%    +0.3%     7 bars
    trail 8%        ~47%    +1.9%    32-35 bars
    fixed 10 bars   ~55%    +0.6%    10 bars

**The trailing stop holds all the money, and it REQUIRES a 32-35 bar hold.**
Forcing the hold to 10 bars costs two thirds of the per-trade return. That is
the family's mechanism, not a tuning artefact — the same way Ichimoku's edge is
its 41+ bar hold.

**G3 is therefore changed from ≤20 bars to ≤40 bars.** This is a deliberate
SCOPE change, declared: momentum is being accepted as a LONGER-HOLD sleeve that
runs alongside the 4-day mean-reversion screen, not as a substitute for it. The
owner asked to explore both. Every other gate is UNCHANGED, and the change is
recorded here before the run rather than applied quietly afterwards.

Moving a threshold to force a pass is forbidden; moving one because the study
proved the question was mis-specified is a different act, and it only counts if
it is written down first. This is that.

## Where v1's best variants actually stood

    pullback to 10SMA / trail 8%   5,745 trades  48.5% win  +1.93%  35b  tail 34%  worst -33.3%
    20-day high      / trail 8%    7,801 trades  45.9% win  +1.82%  32b  tail 38%  worst -32.6%

Under v1 gates both failed G3 (hold) and G5 (worst trade); the 20-day-high
variant also failed G4. With G3 at ≤40, `pullback to 10SMA / trail 8%` clears
V0, G1, G2, G3 and G4 — and fails **G5 alone** (worst −33.3% vs −25%).

## The v2 question

Can a HARD INITIAL STOP fix G5 without destroying the edge? A trailing stop only
engages after a trade has moved in your favour; a run that goes wrong from entry
has nothing to trail from, which is where −33% comes from.

| # | Variant |
|---|---------|
| A | pullback to 10SMA + trail 8% (v1 best, baseline) |
| B | A + hard initial stop −10% |
| C | A + hard initial stop −8% |
| D | A + hard initial stop −6% |
| E | 20-day high + trail 8% + hard stop −8% |

## Gates

| # | Test | Pass | vs v1 |
|---|------|------|-------|
| V0 | Trades | ≥ 1,000 | unchanged |
| G1 | Win rate ≥ 45% | true | unchanged (owner's floor) |
| G2 | Mean return per trade, net | > 0 | unchanged |
| **G3** | **Median hold ≤ 40 bars** | true | **CHANGED from ≤20 — see above** |
| G4 | Top-1% profit share ≤ 35% | true | unchanged |
| G5 | Worst single trade ≥ −25% | true | unchanged |

## Prediction — recorded before the work

**C (hard −8%) passes all six.** A hard stop should cap the worst case near −8%
plus slippage, comfortably inside G5, while costing maybe 3-5 points of win rate
and some per-trade return.

**D (−6%) will pass G5 but FAIL G1** — too tight for a family whose winners take
30+ bars to develop; it will stop out of trades that later work.

**The risk is G4.** As with the mean-reversion stop test, cutting losers early
concentrates the remaining profit into fewer winners and the tail share rises.
If every stopped variant fails G4, the honest conclusion is that momentum cannot
be both capped and diversified on this universe, and the sleeve should be run
small rather than fixed.
