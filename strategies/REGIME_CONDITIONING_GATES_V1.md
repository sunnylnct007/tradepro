# REGIME CONDITIONING GATES V1 — where does the edge actually live?

27 Sep 2026. Prompted by an outside review proposing a six-dimension regime
framework with live sizing rules attached. This takes the half that is
measurable and refuses the half that is not.

## What this is, and what it deliberately is not

**IT IS DESCRIPTIVE.** It tags every trade the two gated rules produce with the
market state at ENTRY and reports performance per state. Nothing is filtered,
no threshold is added to any rule, and no sizing multiplier is invented.

**IT IS NOT A REGIME GATE.** The proposal that prompted this included live
rules of the form `if vol_level == 'high' and trend == 'trend': allow = False`
and `size_mult = 0.5`. Those thresholds are not measured anywhere. Bolting them
onto a gated strategy would mean the strategy no longer matches what was
tested, and every gate result becomes a claim about a rule we do not run. This
desk already carries that scar: the wheel's YELLOW regime "permits a short put
at reduced size" and NOTHING ever defined the reduction, because inventing it
would have been fiction ([[project_yellow_regime_sizing_open_decision]]).

If a cell here shows a rule losing money across thousands of trades in both
halves of the sample, THAT becomes a proposal, pre-registered on its own.

## THE MAIN HAZARD, stated before anything is run

**Multiple testing.** Slicing 21,948 trades by enough dimensions guarantees
some cell looks spectacular by chance. The outside proposal did not mention
this and it is the single thing most likely to produce a beautiful false story.

Three controls, fixed now:

1. **TWO dimensions, not six.** Volatility level and trend strength, both from
   SPY, both computable from data held since 2006. Nine cells, not eighty-one.
2. **THE CELLS ARE NAMED HERE, BEFORE THE RUN.** Any cell not in this document
   is exploratory and must be reported as such.
3. **A CELL NEEDS n ≥ 500 TO BE DISCUSSED AT ALL.** Below that it is reported
   with its n and no interpretation.

## The two dimensions

Both are computed from SPY on an EXPANDING window — every value uses only data
available on that date. No forward fill, no full-sample quantiles.

    VOLATILITY LEVEL   20-day realised vol of SPY, annualised, versus its own
                       trailing 2-year distribution
                         low    bottom tercile
                         med    middle
                         high   top tercile

    TREND STRENGTH     ADX(14) on SPY
                         range    ADX < 20
                         neutral  20 ≤ ADX < 25
                         trend    ADX ≥ 25

Terciles are computed against the TRAILING distribution, not the whole sample.
A full-sample quantile is look-ahead: it labels 2008 "high vol" using 2026 data.

## Gates — what makes this worth acting on
- **V0** at least 6 of the 9 cells carry n ≥ 500 for the swing rule. A grid
  mostly made of thin cells describes noise.
- **G1** the SPREAD across cells exceeds the sampling error. Concretely: the
  best and worst qualifying cell differ in mean return per trade by more than
  2 standard errors of the pooled mean.
- **G2** the ordering HOLDS IN BOTH HALVES of the period. A regime effect that
  appears only in 2006-2016 is a period effect wearing a regime's clothes.
- **G3** the effect is not one symbol or one month — no single symbol
  contributes more than 10% of any cell's trades.

## Prediction (recorded before the run)
**Swing: G1 passes, G2 fails.** I expect mean reversion to look clearly better
in LOW volatility and worse in HIGH, because a 2.25σ dip in a calm market is a
genuine outlier while the same dip in a panic is one of hundreds. I give the
spread passing about 70%.

I expect G2 to fail because the two halves contain structurally different
high-vol episodes — 2008 was a credit event, 2020 a two-month round trip, 2022
a grind. If the ordering does not survive that, the "regime" is really "which
crisis", and this desk has already been caught once treating a crash hedge as a
signal ([[SHORT_SIDE_GATES_V1]]).

**Momentum: I expect the opposite sign.** Trend strength should matter more
than volatility, and momentum should do better at ADX ≥ 25 — that is what the
rule is for. If it does NOT, that is the more interesting finding, because it
would mean the pullback entry is doing something other than riding trends.

**What I am most likely to be wrong about:** I have predicted the DIRECTION of
a conditional effect twice on this desk and been wrong both times — the short
mirror and both sizing studies. Weight this prediction accordingly.

## Consequences, pre-stated
- **All gates pass** → publish the table as CONTEXT on each board: "this rule
  does X in this regime". A gate proposal would then be a SEPARATE
  pre-registration with its own prediction, not a consequence of this one.
- **G1 fails** → the edge does not vary by regime enough to act on. Record it
  and stop. This is a real outcome and the most likely one to be ignored.
- **G2 fails** → it is a period effect, not a regime effect. Say so plainly and
  do not condition anything on it.
- **V0 fails** → the grid is too thin. Report the cells and their n, draw no
  conclusion.
- Nothing here changes either rule, regardless of outcome.

## RESULT — run 27 Sep 2026. ALL GATES PASS, and the dimension that matters is not the one I predicted.

22,254 swing trades falling in a labelled session, 2011-06 → 2026-09. SPY
labelled 3,844 sessions; the window starts in 2011 because the trailing
two-year tercile needs two years of history before it can label anything.

| vol | trend | n | win% | mean/trade | worst |
|---|---|---|---|---|---|
| low | range | 3,418 | 68.9% | +0.57% | −25.3% |
| low | neutral | 1,015 | 70.3% | +0.78% | −22.0% |
| low | trend | 5,159 | 72.1% | +0.95% | −25.4% |
| med | range | 1,948 | 67.7% | +0.27% | −18.9% |
| med | neutral | 572 | 69.4% | +0.81% | −13.6% |
| **med** | **trend** | **4,991** | **76.8%** | **+1.56%** | −32.5% |
| high | range | 1,128 | 62.7% | **−0.15%** | −19.0% |
| high | neutral | 225 | 59.1% | −0.36% | *(thin)* |
| high | trend | 3,798 | 69.8% | +0.81% | −29.9% |

    pooled +0.87%, 71.1% win, 1 s.e. = 0.035%

    V0 PASS (8 of 9 cells) · G1 PASS (spread 1.71% vs 0.07%) ·
    G2 PASS (holds in both halves) · G3 PASS (no symbol >1% of a cell)

### My prediction was wrong twice over
Recorded before the run: *"Swing: G1 passes, G2 fails... mean reversion looks
clearly better in LOW volatility and worse in HIGH."*

G1 passed. **G2 also passed** — the ordering holds in both halves and is not
marginal: early +1.45% vs +0.01%, late +1.62% vs −0.28%. I expected the two
halves to contain structurally different crises and wash the effect out. They
did not.

And the volatility story is not what I said. Low vol is *mediocre* (+0.57% to
+0.95%). The best cell is MEDIUM vol. That is the third time on this desk I
have predicted the direction of a conditional effect and been wrong, which is
now a pattern rather than three misses, and it is the reason the prediction is
written down before the run rather than after.

### What the data actually says: TREND is the dimension, not volatility
Read the table by column and it is unambiguous — in **every** volatility
bucket, trending beats ranging:

    low     range +0.57   →  trend +0.95     (+0.38)
    med     range +0.27   →  trend +1.56     (+1.29)
    high    range −0.15   →  trend +0.81     (+0.96)

**A mean-reversion rule does best when the market is TRENDING.** That reads
backwards until you look at what the rule requires: a name 2.25σ below its
20-day mean *while still above its 200-day average*. In a trending market that
is a genuine pullback inside an intact advance, and it reverts. In a ranging
market the same signal is a name going nowhere, and the 20-day mean it is
reverting to is going nowhere either — the target barely moves, so the trade
grinds to the timeout.

The single negative cell, HIGH VOL + RANGING at −0.15%, is the shape that
destroys this rule: violent, directionless, no advance for the dip to be a
pullback within. It is 1,128 trades — 5% of the sample — and it is the only
condition under which the rule loses money.

### Consequence — publish as CONTEXT, do not gate on it
Per the pre-stated consequence for a full pass: this ships as context on the
board, "the rule does X in this regime", and NOTHING is conditioned on it here.

A gate proposal is a separate pre-registration with its own prediction, and it
would have to answer a question this study cannot: the worst cell still wins
62.7% of the time and loses only 0.15% per trade. Refusing to trade it saves
almost nothing and costs 5% of the sample. **The finding is real and the
obvious action is not.** Recording that gap rather than papering over it is the
point of the pre-stated consequence.

### Momentum
Not graded here. The same harness runs for it, but momentum's own gates were
measured on 256 symbols and it fails G5 on the universe it trades
([[MOMENTUM_GATES_V2]] amendment, 24 Sep) — conditioning a result whose
headline tail is wrong would compound the error rather than illuminate it.
Momentum gets this treatment once its own universe question is settled.
