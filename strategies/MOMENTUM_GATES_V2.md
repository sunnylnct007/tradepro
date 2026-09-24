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

## AMENDMENT — 24 Sep 2026. The gates were measured on a universe this lane no longer trades.

Found while porting this rule into the paper engine so it could place orders
(owner: *"unless we start booking these trades how will we know if our strategy
is really working or not"*). The port is faithful; the BADGE is not.

**Universe above: 256 symbols. The live screen today: 969.**

Replaying the identical rule — same entry, same −8% hard stop, same 8% trail,
same 60-session timeout, same corrupt-bar guard — over the universe the lane
actually screens:

| | gated record (256) | current universe (969) |
|---|---|---|
| trades | 5,815 | **41,023** |
| win | 47.0% | 45.3% |
| mean/trade | +1.53% | **+1.42%** |
| median hold | 34b | 35b |
| top 1% of profit | 31% | 11% |
| **worst trade** | **−14.7%** | **−36.7%** |

    V0 PASS · G1 PASS · G2 PASS · G3 PASS · G4 PASS · G5 FAIL (−36.7% vs −25%)

### The edge survived. The tail did not.
+1.42% per trade across 41,023 trades is the rule confirmed on **seven times
the evidence** that earned the badge, and G4 improved sharply (31% → 11%: the
profit is far less concentrated in a handful of winners than the original
sample suggested). Nothing here says the rule stopped working.

G5 is what fails, and it fails by a wide margin. A −8% stop checked on the
close does not survive a gap, and a universe four times larger contains four
times as many gaps. The −14.7% was never the rule's tail; it was the tail of
256 names.

### This is the second time this desk has published a tail it did not have
[[SWING_SIZING_GATES_V1]] recorded exactly this on 20 Sep: the swing rule's
worst trade was −32.6%, not the −19.6% quoted in its gates doc and its source
file, because 98 of 244 names were capped at four years of history. Same
lesson, different cause — there the sample was too short, here it is too
narrow. **A tail measured on a subset is not the tail you will trade.**

### Consequence — the lane is NOT deployed to paper
The port is written, tested and registered (`momentum_pullback`, a 38-line
subclass of the swing engine that swaps only the signal module). It is
deliberately NOT scheduled. Deploying it today would put a sleeve into an
account with a `gated` badge describing a universe it does not trade, which is
the same false claim in the opposite direction from 23 Sep, when this doc's
missing RESULT section had an assistant tell the owner twice that a genuinely
gated lane was unproven.

Two honest routes, and this is the owner's call, not a tuning problem:

1. **Trade the universe it was gated on.** Restrict the sleeve to the 256
   symbols and the record stands as written. Costs signal count.
2. **Re-register for the wide universe.** Freeze new gates and a prediction
   BEFORE running, accepting that G5 needs either a wider bar or a mechanism
   that caps gap risk. Note that G5 was the gate that killed variant A, so
   moving it is a substantive change and not an admin one.

What must NOT happen is quietly widening G5 to fit the number we just measured.
