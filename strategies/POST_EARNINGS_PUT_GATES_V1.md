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
