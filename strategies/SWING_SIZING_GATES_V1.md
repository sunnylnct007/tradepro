# SWING SIZING GATES V1 — does the live position size survive the real tail?

20 Sep 2026. Yesterday's out-of-sample run
([[SWING_OUT_OF_SAMPLE_GATES_V1]]) confirmed the entry rule on 21,948 unseen
trades and, in the same pass, found that the tail we publish is not the tail
we have: the worst trade is **−32.6%**, not the −19.6% / −17.7% quoted in the
gates doc and in `mean_reversion_swing.py`. Those were the fourth and fifth
worst; the three above them were invisible while 98 of 244 names were capped
at four years of history.

The entry rule is unchanged by that. **The risk model is not**, and the book
is funded at $150,000.

## What the live sleeve does today
Measured from the running daemon, not from the defaults:

    --capital-usd 150000  --max-open-positions 15
    --max-position-pct-of-capital 5        → $7,500 a position
                                           → 75% of capital deployed at full load

## The test — replay, not Monte Carlo
Walk the real history bar by bar over the traded universe, take every signal
the live rule fires, and apply the live constraints: at most 15 concurrent
positions, 5% of *current* equity each, exits on target / −8% close stop / 20
sessions. Compound the result and record the equity curve.

**Deliberately a replay and not a Monte Carlo.** Resampling trades
independently destroys the one property that matters here: mean-reversion
signals ARRIVE IN CLUSTERS, because a market falling two sigma below its mean
does so in many names at once. A bootstrap would spread 2008 evenly across
twenty years and report a drawdown the strategy cannot actually have. This
desk has already recorded that trap once —
[[project_index_strangle_eight_markets]]: *"Monte Carlo on gated trades CANNOT
see a crash."* A date-ordered replay sees exactly one thing: what would have
happened.

Sizes swept: 2%, 3%, 5% (live), 8%, 10% per position, at 15 concurrent.

## Gates — the LIVE size (5% × 15) is adequate only if all hold
- **G0** the replay spans both 2008 and March 2020 with signals firing in each
  — if the sleeve never traded a crisis, the test is vacuous
- **G1** max portfolio drawdown ≤ **25%**
- **G2** worst single day ≤ **10%** of equity
- **G3** the full-period return is positive
- **G4** peak concurrent exposure ≤ **80%** of equity, i.e. the position cap
  actually binds and the sleeve cannot quietly become fully invested

## Prediction (recorded before the run)
**G1 fails at 5%.** I expect a max drawdown of 30–40%.

Reasoning: 15 positions at 5% is 75% deployed, and these are not independent
bets. The rule buys names that have fallen two sigma below their mean while
still above a rising 200-day average — a condition that goes from rare to
universal in a week when an index rolls over. In October 2008 and March 2020 I
expect the sleeve at or near full load with every position correlated, and a
−30% cluster on 75% exposure is a −22% drawdown before compounding or a second
wave. I give "5% survives a 25% drawdown bar" about 25%.

I also expect **G3 to pass comfortably** — the edge is strong and the win rate
is 70%. This study is not about whether the rule makes money. It is about
whether the size makes the drawdown livable.

If that is right, the useful output is not "5% is wrong" but **the largest
size that clears a 25% drawdown**, which is a number the owner can act on.

## Consequences, pre-stated
- **All gates pass at 5%** → live sizing stands; the tail correction changes
  the record, not the risk.
- **G1 fails at 5%** → recommend the largest swept size that passes, and say
  plainly what it costs in return. Sizing is the owner's call; I present the
  curve, I do not change the daemon.
- **G1 fails at every size** → the drawdown is structural, not a sizing
  problem, and the sleeve needs a correlation or regime brake before it is
  widened to the 745 new names.
- **G0 fails** → report it and stop. A risk study that never saw a crisis
  proves nothing, and saying so is the whole point of G0.

Nothing here changes the live daemon. This measures whether its size is sane.

## RESULT — run 20 Sep 2026. The size is fine. The UNIVERSE is the problem.

5,416 signals, 2006-11-01 → 2026-09-17, replayed through the live constraints.
166 signals fired in the 2008 crisis and 163 in the March 2020 crash, so the
sleeve did trade both and G0 is satisfied.

| size | final | return | max DD | worst day | peak exposure |
|---|---|---|---|---|---|
| 2% | 197,311 | +32% | 9.9% | −2.6% | 30% |
| 3% | 224,379 | +50% | 14.5% | −3.9% | 46% |
| **5% (live)** | **285,193** | **+90%** | **23.3%** | **−6.5%** | **77%** |
| 8% | 391,011 | +161% | 35.2% | −10.4% | 125% |
| 10% | 468,160 | +212% | 42.4% | −13.1% | 158% |

**ALL FIVE GATES PASS at the live 5%.** G1 23.3% ≤ 25%, G2 −6.5% ≤ 10%,
G3 +90%, G4 77% ≤ 80%.

### My prediction was wrong
I predicted G1 would FAIL with a 30–40% drawdown and gave "5% survives" about
25%. It passed at 23.3%. I over-weighted the −32.6% single-trade tail and
under-weighted the position cap: 15 × 5% = 75% is a hard ceiling on exposure,
so a correlated cluster cannot compound the way I imagined. The cap, not the
stop, is what makes this survivable — and the cap was already right.

Note what 8% and 10% do: peak exposure 125% and 158%. Fifteen positions at 8%
is 120% of equity, i.e. **margin**. Any size above 6.6% silently levers the
sleeve, which is a far better argument against raising it than the drawdown is.

### The finding that actually matters
At the live size the sleeve returns **3.28% CAGR for a 23.3% drawdown** —
0.14 of return per unit of pain, and less than cash. Not because the edge is
weak (70% win, +0.81%/trade, confirmed out-of-sample yesterday) but because
the capital is almost never working: **1.08 signals per trading day across 244
names**, held at most 20 sessions.

So the constraint is signal SCARCITY, not risk appetite. Replaying the same
rule, the same 5% size and the same 15-position cap over the **989 usable
symbols now in the bar store**:

| universe | signals/day | CAGR | max DD | worst day | peak expo | CAGR/DD |
|---|---|---|---|---|---|---|
| 244 (live) | 1.08 | 3.28% | 23.3% | −6.5% | 77% | 0.14 |
| **989** | **5.46** | **7.77%** | **22.6%** | −6.7% | 77% | **0.34** |

**Return more than doubles and the drawdown does not rise.** The 15-position
cap is the binding constraint, so a larger universe fills the same 15 slots
more often rather than taking more risk — peak exposure is 77% in both. Risk
per unit of return improves 2.4×.

This is the same conclusion yesterday's out-of-sample run reached from the
other direction: those extra names carry the same edge (+0.90%/trade on 21,948
unseen trades). Two pre-registered studies, independent samples, same answer.

### Consequences
1. **Live sizing stands.** 5% × 15 is correct and needs no change. The tail
   correction changed the record, not the risk.
2. **Do not raise the position size.** Above 6.6% the sleeve is on margin, and
   the drawdown gate fails at 8% anyway.
3. **Widening the universe is now supported on BOTH return and risk** — which
   reverses the "blocked on risk" note in [[SWING_OUT_OF_SAMPLE_GATES_V1]],
   written before the drawdown was measured. That was the right caution and it
   has now been answered.
4. Still worth saying plainly: 7.77% CAGR is a good sleeve, not a living. It
   is not $400–500/day, and no sizing in this table gets there without margin.
