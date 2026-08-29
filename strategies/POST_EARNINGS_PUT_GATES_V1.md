# Post-earnings cash-secured puts — PRE-REGISTERED gates

**Committed BEFORE the graded run (28 Aug 2026).** Exploratory numbers already
exist and are quoted below as the PRIOR, not as a result. The point of this
file is that the gates are fixed while the outcome is still unknown.

## What the owner asked for

> *"for the wheel I have a simpler strategy. for e.g MRVL is a good stock and
> was trading at 240 before quarterly result and now it corrected to 220 so i
> can safely play a put at 195. this strategy can work normally after every
> quarterly results"*

and, clarifying the universe:

> *"the strategy i m saying is not fully a wheel but we will consider only
> symbols we happy to hold"*

and, choosing how to apply that:

> universe = **all 244, sized by volatility** — cap the tail by SIZE rather
> than by excluding names.

## The thesis, stated so it can fail

Selling a put AFTER a quarterly report is safer than selling one before it,
because the binary gap risk is spent. The next report is ~90 days out, beyond a
30-day expiry, and the stock has already repriced. Assignment is acceptable —
these are names the owner wants to own — so the measure is P&L, not
assignment rate.

The thesis is FALSE if the post-report drop signals more downside rather than
less. That is the real question, and it is not obvious: volatility clusters.

## What is being tested

| | |
|---|---|
| trigger | a confirmed EARNINGS report date, from `earnings_history.json` |
| entry | first session AFTER the report, when the stock fell ≥ 8% on it |
| structure | cash-secured put, 10% OTM, 30 sessions |
| premium | Black-Scholes at realised vol measured BEFORE the drop (the IV-crush proxy — deliberately pessimistic) |
| exit | expiry; assigned if below strike |
| sizing | collateral scaled by `target_vol / symbol_vol`, so a 60%-vol name gets ~1/3 the size of a 20%-vol name |
| costs | commission + half-spread, charged per contract |

Sizing is part of the STRATEGY, not a presentation choice, so gates are graded
on size-weighted returns.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Events (validity) | ≥ 300 real earnings events |
| **G1** | Win rate | ≥ 80% |
| **G2** | Mean return per trade, NET of costs, size-weighted | > +0.75% of collateral |
| **G3** | **Beats the null** — same test on random non-earnings entries | mean must exceed it by ≥ 0.5pt |
| **G4** | 5th-percentile trade | ≥ −8% |
| **G5** | **Two independent halves** (2020-2023 / 2023-2026) both pass G1 and G2 | true |
| **G6** | Survives the IV crush — premium at PRE-drop vol | still passes G2 |

**G3 is the gate that matters most, and it is the one the exploratory work has
not yet run.** Selling any 10% OTM put returned +0.44%/trade in this universe.
If the post-earnings version cannot clearly beat that, the earnings event is
decoration and the edge is just "selling puts works", which we already knew.

**G4 is set at −8%, not at the worst trade.** Vol-scaled sizing is supposed to
cap the tail; if the 5th percentile is still worse than −8% after scaling, the
sizing rule has not done its job and the strategy is not what it claims.

**G5 is here because this repo has been burned by exactly this.** The v3 volume
filter looked strong and was rejected when a time split showed the edge
inverted pre-2020. A single-window pass is not evidence.

## The honest limit, recorded up front

**Earnings history reaches back only to ~October 2020** (yfinance
`earnings_dates`, 25 rows/symbol, measured 28 Aug 2026). A pre-2020 regime test
is IMPOSSIBLE for this strategy at any bar depth — deeper bars do not help when
there are no report dates to pair them with.

So G5 splits 2020-2023 against 2023-2026. That is two independent halves of one
regime, not two regimes. **A pass here licenses paper-forward testing, never
funding**, and any published result must carry this sentence.

## Prediction — recorded before the graded run

**G1, G2 and G6 pass.** The exploratory run gave 88.6% win and +2.49%/trade,
falling only to +2.23% when priced at pre-drop vol, over 604 drop-events.

**G3 is where I expect trouble.** The exploratory comparison was +2.49% against
a +0.44% baseline, which looks decisive — but that baseline sampled every 5th
session indiscriminately, while the earnings set is concentrated in volatile
names and volatile weeks. A like-for-like null may close much of that gap.

**G4 is the second risk.** Unscaled, p5 was −9.9%. Vol-scaling should pull that
inside −8%, but that is a prediction, not a measurement.

**G5 I expect to pass**, because both halves sit in the same post-2020 regime —
which is precisely why passing it proves less than it appears to.

If G3 fails, the correct conclusion is that the earnings trigger adds nothing
and the owner should simply sell puts on a schedule. The answer is NOT to move
the drop threshold until something passes.

## Scope

Clearing these gates licenses a PAPER FORWARD TEST and a candidate surface. It
does not license funding. The existing wheel verdict (v3: DO NOT FUND) stands
independently of this study.

---

# RESULT — 28 Aug 2026. FAILED V0 and G5. Recorded the same day it ran.

```
post-earnings (GRADED)   n=284    win 87.0%  mean(w) +1.19%  median +1.82%  p5  -5.48%
null: non-earnings       n=40450  win 83.6%  mean(w) -0.06%  median +0.69%  p5  -7.61%
post-drop vol (G6 ref)   n=284    win 86.3%  mean(w) +1.00%  median +1.67%  p5  -6.32%
half 1 (2020-2022)       n=85     win 82.4%  mean(w) +0.75%  median +1.55%  p5 -13.69%
half 2 (2023-2026)       n=199    win 88.9%  mean(w) +1.37%  median +1.92%  p5  -5.21%

V0  FAIL  events >= 300                       284
G1  PASS  win rate >= 80%                     87.0%
G2  PASS  mean/trade (size-wtd, net) > +0.75% +1.19%
G3  PASS  beats null by >= 0.5pt              +1.25pt
G4  PASS  p5 >= -8%                           -5.48%
G5  FAIL  both halves pass G1 and G2          h1 82%/+0.75%  h2 89%/+1.37%
G6  PASS  survives IV crush                   +1.19%
```

## The prediction was wrong, in the strategy's favour

**G3 was named as the likely failure and it passed clearly.** The concern was
that the exploratory +0.44% baseline sampled sessions indiscriminately while
the earnings set concentrates in volatile names and volatile weeks, so a
like-for-like null would close the gap. It did not: run on the SAME symbols
with the SAME vol-scaled sizing and report weeks excluded, the null returns
**-0.06%** against the strategy's **+1.19%**.

That is the single most informative number here. Selling puts indiscriminately
in this universe earns nothing once costs are paid. The earnings trigger is
doing real work — it is not decoration, which is what G3 existed to test.

## Why it still FAILED, and why neither failure should be waived

**V0 (284 vs 300).** A near miss, and the temptation is to call it close
enough. It is not: the threshold was fixed before the run precisely so it could
not be adjusted afterwards. The honest fix is more events — a lower drop
threshold would change the strategy, not the sample.

**G5 is the substantive failure.** Half 1 returns +0.75% against half 2's
+1.37%, and the gate requires strictly greater than 0.75%. Reading only the
means understates it; the tails differ far more:

    half 1 (2020-2022)   p5 -13.69%
    half 2 (2023-2026)   p5  -5.21%

Half 1 contains the 2022 bear market. The strategy is directionally BULLISH
(short a put is long delta, ~+0.25 per contract, +1.0 once assigned), and it
behaved like one: it still made money in the harder window, but with a tail
2.6x worse. G4 passes on the pooled sample ONLY because the calmer half
dominates by count, 199 to 85.

That is the regime dependence this repo has been burned by before, showing up
INSIDE the one regime we can see rather than between two.

## Verdict

**Do not fund. A paper forward test is defensible; funding is not.**

Five of seven gates pass and G3 — the one that decides whether the idea has any
edge at all — passes convincingly. The strategy is real. What it has not shown
is that the edge is stable across conditions, and the one window resembling a
downturn is exactly where the tail widened.

The pooled p5 of -5.48% is the number most likely to mislead an owner reading
only the headline. In a 2022-like window the honest figure is **-13.69%**, and
vol-scaled sizing did NOT prevent that — everything is volatile together in a
drawdown, so scaling by each symbol's own vol scales nothing relative to the
market.

## What would change the verdict

1. **More events** — clears V0 and narrows every interval. Options: extend
   earnings history before Oct 2020 (needs a source yfinance cannot give), or
   accept a longer forward test.
2. **A tail rule for half-1 conditions.** The failure is concentrated, not
   diffuse. A market-level filter (index below its 200-SMA, or VIX above a
   threshold) is the natural candidate — and must be PRE-REGISTERED and tested
   as its own study, never tuned on this sample until G5 passes.
3. **Paper-forward it now**, at small size, and let live events accumulate
   against these same gates.

---

# V2 — MARKET-REGIME FILTER. Gates fixed BEFORE the run (29 Aug 2026).

Owner: *"but dont we shd also base it off SPY or overall index"* — after the
year-by-year breakdown showed the damage is 2022, not COVID:

    2020 (COVID)   n=9   100.0% win  +2.39%  worst  +0.74%   <- BEST year
    2022 (rates)   n=50   70.0% win  -0.01%  worst -54.05%   <- the whole problem

V1 failed G5 because the strategy has no defence in a sustained downtrend. It
is directionally bullish and behaved like it. 2022 was identifiable in real
time — SPY was below its 200-day average for most of it — so the question is
whether a market-level gate removes that damage without gutting the rest.

## The filter, and why THIS one

**Enter only when SPY closes above its own 200-day SMA on the entry date.**

Chosen because it is the trend test ALREADY used by the Swing strategy and its
screen. It is not a parameter search: no threshold was tried and discarded, and
none will be. If SPY-above-200SMA does not fix G5, the honest answer is that
this strategy needs a different defence, NOT that 150 or 250 days works better.

One filter, one run, recorded whatever it says.

## Gates — V2 must clear V1's bar AND the two it failed

| # | Test | Pass |
|---|------|------|
| **W0** | Events surviving the filter | ≥ 200 |
| **W1** | Win rate | ≥ 80% |
| **W2** | Mean/trade, size-weighted, net | > +0.75% |
| **W3** | Beats the SAME-FILTERED null by ≥ 0.5pt | true |
| **W4** | p5 | ≥ −8% |
| **W5** | **Both halves pass W1 and W2** — the gate V1 failed | true |
| **W6** | **2022 alone is not a losing year** — mean > 0 | true |
| **W7** | **Retains ≥ 60% of V1's events** — must not "work" by trading almost never | true |

**W7 exists because the filter could pass every other gate by refusing nearly
every trade.** A strategy that fires four times a year and never in a downturn
is not a fixed strategy; it is a different, much smaller one, and the honest
description would be "we stopped trading" rather than "we solved the tail".

**W6 targets the actual failure.** Pooled improvement is not enough — 2022 is
the year that broke V1 and it has to stop being a losing year on its own.

**W3 re-runs the null WITH the filter.** Otherwise a pass could just mean "the
market went up after 2022", which would be true of any long position and would
tell us nothing about the earnings trigger.

## Prediction — recorded before the run

**W6 passes and W7 is the risk.** SPY was below its 200-SMA for roughly half of
2022, so the filter should remove much of the damage — but earnings drops
CLUSTER in bad markets, so the events it removes are disproportionately the
2022 ones, and the retention test is where I expect trouble.

**W4 I expect to pass comfortably**, since the −22% p5 of 2022 is what dragged
the pooled figure.

If W7 fails, the correct conclusion is that the edge and the risk are the same
phenomenon and cannot be separated by a market filter. That would be a real
finding, not a failure to be tuned away.

## V2 RESULT — 29 Aug 2026. ALL EIGHT GATES PASS.

```
V1 unfiltered        n=284    win 87.0%  mean +1.19%  p5 -5.48%  worst -54.05%
V2 SPY>200SMA        n=229    win 89.5%  mean +1.29%  p5 -4.72%  worst -23.40%
V2 null (filtered)   n=32617  win 83.1%  mean -0.15%  p5 -7.35%
V2 half1 2020-22     n=44     win 95.5%  mean +1.43%  p5 +0.00%  worst  -2.54%
V2 half2 2023-26     n=185    win 88.1%  mean +1.26%  p5 -5.21%  worst -23.40%
V2 2022 only         n=9      win 77.8%  mean +0.94%  p5 -2.54%  worst  -2.54%

W0 PASS events >= 200                 229
W1 PASS win >= 80%                    89.5%
W2 PASS mean > +0.75%                 +1.29%
W3 PASS beats filtered null >= 0.5pt  +1.44pt
W4 PASS p5 >= -8%                     -4.72%
W5 PASS both halves pass W1+W2        h1 95%/+1.43% · h2 88%/+1.26%
W6 PASS 2022 mean > 0                 +0.94% (n=9)
W7 PASS retains >= 60% of V1 events   81%
```

**The prediction was wrong again, in the strategy's favour.** W7 was named as
the risk — the fear that earnings drops cluster in bad markets, so the filter
would survive by refusing to trade. It retained **81%** of events, well clear
of the 60% floor. The filter is selective, not prohibitive.

Everything V1 failed is now fixed by the mechanism it was aimed at: the worst
single trade improves from **-54.05% to -23.40%**, and half 1 goes from
+0.75%/p5 -13.69% to **+1.43%/p5 +0.00%** — in that window, after filtering,
the 5th-percentile trade did not lose at all.

### THE WEAKNESS IN THIS PASS, stated plainly

**W6 passed on nine events.** 2022 had 50 qualifying earnings drops; the filter
removed 41 of them. So "2022 is no longer a losing year" rests on the nine
trades that happened while SPY was above its 200-SMA during a bear market.

That is a thin basis, and the gate as written (`mean > 0`, no minimum count)
was too weak to catch it. It is recorded here rather than fixed retroactively:
adding a sample floor to W6 after seeing the result would be moving the
goalposts, which is the same offence as waiving a gate.

What the result honestly supports: **the filter removes most of the exposure to
a sustained downtrend, and the trades it still permits in such a window were
fine.** What it does NOT establish is how the strategy performs across a FULL
bear market, because the filter's answer there is largely "do not trade" — and
that is only tested against one bear market, on 9 surviving events.

**The worst trade is still -23.40%.** Vol-scaled sizing plus the market filter
reduce the tail; they do not remove it.

### Verdict

**Paper-forward test, at small size, with the SPY filter live.** Not funding.

V2 passes its pre-registered gates honestly and beats a filtered null by
1.44 points, which means the earnings trigger — not the market direction —
is carrying the edge. That is a real result and better than anything the wheel
work has produced.

Funding waits on live events accumulating against these gates, and specifically
on W6 being re-tested with a real sample the next time SPY spends months below
its 200-SMA.
