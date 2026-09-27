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
