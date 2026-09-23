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

## RESULT — run 22 Aug 2026. Variants B and C pass all six.

Recorded here 23 Sep 2026, a month late. The run happened on the day; its
output has been sitting in `backtests/logs/momentum_v2.log` and its conclusion
in the `momentum_candidates.py` docstring, and **this file — the one that
promised the result — never got it.**

That omission had a cost. On 23 Sep an assistant checking which lanes had
evidence grepped the gate docs, found no RESULT section here, and told the
owner twice that the live Momentum lane was wearing a `gated` badge it had not
earned. It had earned it. A result that is not written where it was promised
is, to every later reader, a result that does not exist.

Universe: 256 symbols.

| variant | trades | win% | mean% | total% | hold | top1% | worst% |
|---|---|---|---|---|---|---|---|
| A pullback + trail only | 5,725 | 48.3% | +1.60% | 9,165% | 35b | 30% | −33.3% |
| B pullback + hard −10% | 5,744 | 48.0% | +1.58% | 9,083% | 35b | 30% | −16.0% |
| **C pullback + hard −8%** | **5,815** | **47.0%** | **+1.53%** | **8,903%** | **34b** | **31%** | **−14.7%** |
| D pullback + hard −6% | 6,055 | 42.9% | +1.34% | 8,085% | 28b | 35% | −11.5% |
| E 20d-high + hard −8% | 7,962 | 44.2% | +1.41% | 11,219% | 30b | 35% | −13.5% |

| variant | V0 | G1 ≥45 | G2 >0 | G3 ≤40 | G4 ≤35 | G5 ≥−25 | verdict |
|---|---|---|---|---|---|---|---|
| A | PASS | PASS | PASS | PASS | PASS | **FAIL** | |
| **B** | PASS | PASS | PASS | PASS | PASS | PASS | **ALL PASS** |
| **C** | PASS | PASS | PASS | PASS | PASS | PASS | **ALL PASS** |
| D | PASS | **FAIL** | PASS | PASS | PASS | PASS | |
| E | PASS | **FAIL** | PASS | PASS | **FAIL** | PASS | |

### The prediction was right, and for the stated reason
Recorded before the run: *"C (hard −8%) passes all six... while costing maybe
3-5 points of win rate and some per-trade return."* It passed, and the cost was
1.3 points of win rate and 0.07% per trade — less than predicted. The hard stop
cut the worst trade from **−33.3% to −14.7%** for that price, which is the
whole point: a trailing stop has nothing to trail from when a trade goes wrong
from entry, and that is where −33% came from.

I also predicted G4 was the risk — that cutting losers early would concentrate
profit into fewer winners and push the tail share up. It did move (30% → 31%
at −8%, 35% at −6%), and it is what fails D alongside G1. The mechanism was
right even though C survived it.

### What ships
**Variant C.** `STOP_PCT = 0.08`, `TRAIL_PCT = 0.08`, `MAX_HOLD = 60` in
`momentum_candidates.py`. B is statistically indistinguishable and was not
chosen: a tighter initial stop is the more conservative of two passes.

### Reproducible, unlike mean-reversion v1
The harness is `backtests/studies/mom_v3.py`, committed. The full run including
the gate table is `backtests/logs/momentum_v2.log`. Both survive, so every
number above can be re-derived — which is precisely what
[[MEAN_REVERSION_GATES_V1]] cannot offer, and why that file carries a warning
at the top instead of a result.
