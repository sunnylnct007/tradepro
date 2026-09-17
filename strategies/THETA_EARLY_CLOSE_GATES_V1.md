# THETA EARLY-CLOSE GATES V1 — does closing a short put early beat holding it?

16 Sep 2026. Owner's proposal, verbatim: *"we should be able to leverage theta
decay a bit better. maybe sell a bit more far duration and close it a lot more
early but sell more lots."*

I answered it first with a Black-Scholes model, which said: closing early is
right, **longer duration is backwards** (premium scales with √T so short
duration dominates annualised yield), and "more lots" is capped by collateral.
That answer is a model. This study replaces the parts of it that our own
stored prices can actually settle.

## What we can and cannot settle

We hold ~24 days of daily option chains (from 13 Aug) with bid, ask, delta, IV
and spot per leg. 888 put contracts carry 5–11 daily observations, so a
**5-to-11-session holding window is measurable on real quotes, including the
spread paid twice.**

We CANNOT settle: holds to expiry, cycles longer than ~11 sessions, any
regime other than mid-Aug to mid-Sep 2026, or assignment outcomes. Anything
claimed about those remains model, and must be labelled as such.

## The measurement
For each put contract observed on day 0 and again on day N: sell at the **bid**
on day 0, buy back at the **ask** on day N. That is the pessimistic side of
both spreads — what a market order actually pays — and it is the cost the toy
model ignored. Annualise by N sessions / 252.

Report by holding length N and by the contract's DTE at entry.

## Gates — the owner's proposal is SUPPORTED only if
G0 n ≥ 300 contract-pairs at the tested horizon
G1 early close (N ≈ 5) beats the same contracts held to the last observation,
   on **net** annualised return after both spreads
G2 the advantage survives per-symbol: it holds in ≥ 60% of symbols
   individually, so one name cannot carry it
G3 longer DTE at entry does NOT beat shorter DTE on net annualised return —
   this is the half of the proposal the model says is backwards, and it is
   tested rather than assumed

## Prediction (recorded before the run)
G1 PASSES — early close beats holding, as the model said. G3 FAILS TO
OVERTURN the model, i.e. longer duration will still look worse.

But I predict the **effect is much smaller than the model suggests**, because
the model assumed a $10 spread and these are real quotes. The spread is paid
twice regardless of how long you hold, so a short hold amortises it over
fewer days — that is the one mechanism that could genuinely reverse "shorter
is better", and it is exactly what a model with a fixed cost assumption
cannot see. I give ~35% that the net result is a WASH (early-close advantage
under 2pp annualised), which would make the whole proposal noise.

## Consequence, pre-stated
- G0-G2 pass → early closing is real on our own prices; it becomes a
  documented parameter for the put lane, at the measured horizon ONLY.
- Wash → the proposal is retired as unmeasurable at our size, and we stop
  claiming theta management is an available edge.
- Any claim about far duration remains MODEL-ONLY regardless of outcome —
  our data cannot reach it.

## RESULT — run 16 Sep 2026 on 7,809 contract-pairs from our own captures

**The answer depends entirely on how you fill, and my gates did not condition
on that. That is the finding.**

### At mid (limit fills) — the owner's instinct is RIGHT
| hold | n | mid-to-mid net ann. |
|---|---|---|
| 1 session | 1433 | **+96.3%** |
| 3 sessions | 1537 | +27.4% |
| 8 sessions | 241 | +39.2% |
| 15 sessions | 131 | +8.3% |

Decay per day is steepest at the front. Selling short-dated and closing early
is the highest-yielding thing in the data, exactly as the Black-Scholes answer
said and exactly as the owner proposed.

### Crossing the spread destroys it
Median bid-ask is **8.9% of mid** (mean 28.2% — a vicious tail).

| hold | market orders | mid fills | cost of crossing |
|---|---|---|---|
| 1 session | **−97.0%** | +96.3% | −193.3% |
| 3 sessions | −37.6% | +27.4% | −65.0% |
| 6 sessions | −35.9% | −1.0% | −34.9% |
| 8 sessions | **+25.3%** | +39.2% | −13.9% |
| 10 sessions | +11.7% | +25.5% | −13.8% |

The round-trip cost is fixed; holding longer amortises it. Paying the spread
to harvest one day of theta is a **193% annualised** headwind against a 96%
tailwind. It is not close.

### Gates
- **G0 PASS** — 7,809 pairs.
- **G1 FAILS on market orders, PASSES at mid.** Not a single verdict. The gate
  was mis-specified: it asked "does early close beat holding" without naming
  the fill assumption, and the sign flips on it.
- **G2 PASS, in the direction OPPOSITE the proposal** — on market fills,
  longer holds beat short ones in **16 of 18 symbols (89%)**.
- **G3 untested** — DTE at entry is confounded with holding length in a
  24-day window. Far duration remains MODEL-ONLY, as pre-stated.

### The confound check, which matters more than the gates
Short-put P&L over this month was dominated by direction, not decay:

| underlying over the hold | n | mean ann. | win rate |
|---|---|---|---|
| UP | 3513 | +60.1% | **73%** |
| DOWN | 4296 | −133.0% | **8%** |

Mean drift across all holds was +0.28%. An 8% win rate when the stock falls is
the whole risk of this trade in one number. **Selling puts is not an income
trade that theta pays you for; it is a directional bet that pays a premium.**

## Where my prediction was wrong
I predicted G1 would PASS and gave ~35% to a wash. Neither happened: the
result is a **sign flip conditional on execution**, which my prediction did not
contemplate because I wrote it as though fill quality were a detail. I did
name the mechanism — "the spread is paid twice regardless of how long you
hold" — and then failed to make it the axis of the study. The model I gave the
owner assumed a flat $10 spread; the real median is 8.9% of mid, and on the
tail 28%.

## Consequence
1. **Never cross the spread on an option.** Limit at mid, or do not trade. This
   is worth more than any parameter on the lane.
2. If a mid fill is not achievable in a name, **short-duration round trips are
   not available there at all** — its spread eats more than a week of decay.
3. Liquidity screening is not a nicety: the mean/median spread gap says a
   minority of names carry a ruinous tail.
4. "Sell more lots" remains capped by cash-secured collateral and is not
   supported by anything here.
5. Far duration remains unmeasured and MODEL-ONLY.
