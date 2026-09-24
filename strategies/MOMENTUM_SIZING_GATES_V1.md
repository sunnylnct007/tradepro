# MOMENTUM SIZING GATES V1 — is the gap tail survivable at a portfolio level?

24 Sep 2026. Owner, on the G5 failure: *"ok lets not widen it for now"*.

G5 stays exactly where it is. This does not touch it. It asks the different
question G5 cannot answer.

## Why a second study rather than a verdict

[[MOMENTUM_GATES_V2]]'s 24 Sep amendment found the rule FAILS G5 on the
universe it actually screens: worst trade **−36.7%** against a −25% bar, where
the badge claims −14.7%. Two routes were put to the owner — trade the 256
symbols it was gated on, or re-register. The owner declined to move G5.

Diagnosing the tail closed off the first route as well:

    34 of 41,023 trades breach −25%            = 0.08%
    spread across 28 DIFFERENT symbols          — not a class
    29 of the 34 exited on a bar that GAPPED worse than −8%
    worst offenders include DIA, the Dow Jones ETF — maximally liquid

**The tail is overnight gap risk, not bad names.** No liquidity or universe
filter removes it, and a −8% stop checked on the close cannot cap it by
construction. Restricting to the gated 256 would not make the rule safer; it
would return the tail to a sample too small to contain one, which is precisely
how the −14.7% came to be published.

So the honest question is not "which universe hides this" but **"what does a
1-in-1,200 −36% trade do to the book at live size"** — which is a portfolio
question, and exactly what [[SWING_SIZING_GATES_V1]] answered for Swing.

## The test — replay, not Monte Carlo
Identical harness and method to the swing sizing study, so the two sleeves are
directly comparable. Walk the real history bar by bar, take every signal the
rule fires, apply live constraints: at most 15 concurrent positions, a fixed %
of *current* equity each, exits on the rule's own hard stop / 8% trail / 60
sessions. Compound and record the equity curve.

Deliberately a REPLAY. Resampling trades independently would destroy the
property that decides this: momentum signals arrive in clusters, because a
market pulling back to its 10-day average does so in many names at once, and
gap events cluster hardest of all. A bootstrap would spread 2008 evenly across
twenty years and report a drawdown the strategy cannot have.

Sizes swept: 2%, 3%, 5% (swing's live size), 8%, 10%, at 15 concurrent.

## Gates — momentum is sizeable only if ALL hold
Same thresholds as the swing sizing study. Neither sleeve gets an easier test.

- **G0** the replay spans 2008 and March 2020 with signals firing in each
- **G1** max portfolio drawdown ≤ **25%**
- **G2** worst single day ≤ **10%** of equity
- **G3** full-period return positive
- **G4** peak concurrent exposure ≤ **80%** of equity — the cap must bind
- **G5** (added here, and the reason this study exists) the worst SINGLE DAY
  must not exceed G2 *because of one position*. If one −36% gap alone moves the
  book more than 10%, the per-trade tail is a sizing problem and no drawdown
  average can excuse it.

## Prediction (recorded before the run)
**All five pass at 5%, and G1 comes in materially BETTER than swing's 23.3%.**
I give "5% clears a 25% drawdown" about 75% — the opposite direction from my
swing prediction, which was wrong.

Reasoning: momentum holds ~35 bars against swing's ~10, so fewer positions turn
over and exposure is smoother, and the 8% trail exits winners gradually rather
than all at one target. The clustering argument that hurt swing is weaker here
— a pullback-to-the-10-SMA in an uptrend requires an UPTREND, which is exactly
what a crash removes, so I expect momentum to be mostly FLAT in 2008 and 2020
rather than fully loaded. That is also the risk to G0: if it never traded a
crisis, this study proves nothing and must say so.

**At 5%, one −36.7% trade is −1.8% of equity.** That is uncomfortable, not
ruinous, and I expect G5-here to pass comfortably. If it fails, the answer is a
smaller size, not a different universe.

## Consequences, pre-stated
- **All pass at 5%** → the per-trade G5 failure is a disclosure, not a blocker:
  the sleeve is sizeable, the tail is published honestly, and the lane can be
  scheduled to paper with its real tail on the badge.
- **G1 fails at 5%** → recommend the largest swept size that passes and say
  what it costs. Sizing is the owner's call; I present the curve.
- **G1 fails at every size** → the gap tail is structural and momentum should
  not be funded regardless of universe. Record it and stop.
- **G0 fails** → report it and stop. A risk study that never saw a crisis
  proves nothing, and saying so is the whole point of G0.
- Nothing here changes MOMENTUM_GATES_V2's per-trade G5, which stands FAILED
  on the wide universe either way.

## RESULT — run 24 Sep 2026. G1 FAILS at 5%. 3% is the answer, and my prediction was wrong again.

41,023 signals, 2006-11-01 → 2026-09-16, replayed through live constraints
using the swing study's own `replay` function.

**G0 satisfied, emphatically: 1,071 signals fired in 2008 and 184 in the
Feb–Apr 2020 crash.** The sleeve did trade both crises.

| size | return | max DD | worst day | peak exposure | |
|---|---|---|---|---|---|
| 2.0% | +76% | 17.0% | −2.1% | 30% | all pass |
| 2.5% | +102% | 20.8% | −2.6% | 38% | all pass |
| **3.0%** | **+130%** | **24.5%** | **−3.1%** | **46%** | **all pass** |
| 3.5% | +161% | 28.0% | −3.6% | 54% | fails G1 |
| 5.0% (swing's live size) | +276% | **37.6%** | −5.2% | 78% | **fails G1** |
| 8% | +621% | 53.5% | −8.4% | 127% | fails G1 — and on MARGIN |

    at 5%:  G0 PASS · G1 FAIL (37.6% vs 25%) · G2 PASS · G3 PASS · G4 PASS · G5 PASS

### My prediction was wrong, and the reasoning was wrong too
Recorded before the run: *"All five pass at 5%, and G1 comes in materially
BETTER than swing's 23.3%... I give it about 75%."*

G1 failed at **37.6%**, materially WORSE than swing's 23.3%. Second sizing
study in a row where my drawdown call was wrong; on swing I was too pessimistic
and here too optimistic, which is worth noting as a pattern rather than two
separate misses.

The reasoning failed at its core claim. I argued momentum would be *"mostly
FLAT in 2008 and 2020, because a pullback-to-the-10-SMA in an uptrend requires
an UPTREND, which is exactly what a crash removes."* It fired **1,071 times in
2008** — six times swing's 166. The 200-day average LAGS, so for months into a
decline names still sit above it and every bounce to the 10-SMA reads as a
pullback in an uptrend. The rule cannot tell the start of a bear market from a
dip, and that is precisely when it trades most.

I also had the hold backwards. I claimed a ~35-bar hold would make exposure
*smoother* than swing's ~10. It does the opposite in a crash: positions opened
into the decline stay open through it, so the sleeve is still holding when the
next leg comes. Longer hold is more time exposed, not less.

### What actually decides it
The per-trade tail was never the problem. **G5-here PASSES comfortably** — the
worst trade, −36.7%, costs 1.83% of equity at 5% and 1.10% at 3%. One gap
cannot hurt the book.

What hurts the book is CONCURRENCY IN A DOWNTREND: many correlated names
firing at once while the 200-day filter is still stale, each held five weeks.
That is a drawdown problem, and drawdown problems are solved by size.

### Consequence — per the pre-stated rule for a G1 failure
**Recommend 3%, and say what it costs.** 3% is the largest swept size clearing
a 25% drawdown: +130% over the period at a 24.5% max drawdown, against +276%
at 5% for a 37.6% drawdown. The owner gives up a little over half the return to
halve the pain. Sizing is the owner's call; this presents the curve and does
not change any daemon.

Note also that momentum at 3% × 15 peaks at 46% exposure, against swing's 77%
at 5% × 15 — so the two sleeves can run together without the book being fully
invested, which a naive 5% for both would not allow.

### What this does NOT do
It does not rehabilitate [[MOMENTUM_GATES_V2]]'s per-trade G5, which stands
FAILED at −36.7% on the wide universe. The badge must continue to say so. This
study says the tail is survivable at 3%, not that it is not there.
