# Equity lane — pre-registered gates, V1

**Committed BEFORE the run.** Thresholds, controls and a prediction are in this
file and in git history before a single number is computed. If the rule fails,
that is the answer; this document does not get a V2 with a lower bar.

---

## Why there is an equity lane at all

Swing (mean reversion) and momentum both ran live and were wound down on
20 Oct 2026. `ENTRY_EDGE_GATES_V1` graded their **entries** against two
controls and returned **negative on 8 of 8 strategy × horizon combinations**.
The finding that matters was not "they lost" — it was *why*:

> the signals' forward returns were negative while the same names'
> unconditional returns were positive: the rules bought the moments those
> names did not work.

So the bar is set by that result. **An equity rule has to beat owning the same
names and doing nothing.** Anything less is an expensive way to underperform a
buy order.

## What is being tested

One rule, deliberately the simplest thing with a real prior behind it:

> **Hold a large-cap name while it is above its 200-day simple moving average.
> Exit when it closes below. Equal-weight across whatever qualifies.
> Rebalance monthly.**

No stop, no target, no sizing cleverness, no regime overlay. Those are all
things that can be added later *if the base rule clears* — adding them first is
how a failing idea gets dressed up until it passes.

**Universe**: the 100-name derived sleeve (`universe/wheel_sleeve.json`) —
price ≥ $20, market cap ≥ $30bn, USD-reporting, which is the owner's stated
"no penny stocks, no small caps" in the market's own vocabulary.

**Data**: 2,752 daily bars per name, 2015-11-27 → 2026-11-06. Eleven years,
containing 2018Q4, the 2020 crash, the 2022 bear market and 2025-26.

**Costs**: 5bps per side. The universe is mega-cap and liquid; this is
deliberately not generous.

## The controls — a rule must beat BOTH

| | control | strips out |
|---|---|---|
| **A** | buy-and-hold the SAME universe, equal weight, same dates | stock selection — the thing that beat us last time |
| **B** | SPY over identical dates | the market itself |

`ENTRY_EDGE_GATES_V1` failed precisely because it did not clear Control A.
Repeating that mistake would make this whole exercise theatre.

## Gates

| id | window | measure | bar |
|---|---|---|---|
| G1 | Full | Net CAGR − Control A CAGR | **≥ +1.0 pt/yr** |
| G2 | Full | Net CAGR − Control B (SPY) CAGR | **≥ 0.0 pt/yr** |
| G3 | Full | Max drawdown | **≤ Control A max drawdown** |
| G4 | 2022 | Net return − Control A | **≥ +5 pts** |
| G5 | 2020 | Max drawdown | **≤ 30%** |
| G6 | Full | Turnover | **≤ 6 round trips per name per year** |

**G3 is the real question.** A trend filter is supposed to buy its
underperformance in bull markets back with a smaller drawdown. If it gives up
return AND does not reduce drawdown, it has no reason to exist.

**G6 exists because a rule that trades constantly will be eaten by costs and
by slippage we cannot model honestly at this granularity.**

## Expected outcome — recorded before the run

Stated plainly so it can be wrong in public:

- **G2 probably PASSES.** A 200-day filter on mega-caps has beaten SPY over
  this period in most published forms, largely by sidestepping 2022.
- **G1 is where this is decided, and I predict it FAILS.** Control A is not
  the market — it is an equal-weight basket of the *same* large, liquid,
  trending names. A trend filter on names that mostly trend up spends its life
  selling dips and buying them back higher. My honest expectation is that it
  trails Control A on CAGR by 1–3 points.
- **G3 probably PASSES**, and that is the tension: lower return, lower
  drawdown. If G1 fails and G3 passes, the rule is a *risk control*, not an
  edge — the same thing the spread routing turned out to be.
- G4 should pass (2022 is where a trend filter earns its keep).
- G5 is the one I am least sure of: March 2020 fell too fast for a 200-day
  filter to help, and the exit will have fired near the bottom.

**If G1 fails, the answer is that timing these names does not pay, and the
equity lane should be an ownership decision rather than a trading one.** That
would be a real finding, not a failure to find one, and it should be published
as such rather than re-run with a different lookback until something passes.

## What closes this

G1–G6 all passing **on these numbers as committed**. Nothing else closes it.

A pass does not mean funding: it means the rule has earned a forward test on
paper, which is the only evidence that is not curve-fitted by construction.
