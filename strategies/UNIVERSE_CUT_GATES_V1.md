# UNIVERSE CUT GATES V1 — does cutting junk names fix the tail, and is "too extended" real?

3 Oct 2026 (Saturday). Owner: *"we need to cut some more symbols so we reduce
noise... some fundamentally weak signals we don't need to even consider"* and
*"even let's say momentum trade we need to know if it's already run too deep
to enter or is there a fresh entry."*

Committed BEFORE the run, per house discipline.

## Why liquidity, not fundamentals, for the historical test

The owner's instinct is a fundamentals floor. We cannot test that honestly
yet: we hold no point-in-time fundamentals, and applying today's P/E to a
2012 trade manufactures look-ahead. What we CAN test with zero look-ahead is
**point-in-time dollar liquidity**, computed from the bars themselves at each
signal date. It is also the criterion most plausibly connected to the damage:
momentum's one standing badge failure (G5, worst −36.7% on the full 969-name
universe) lives in gap-prone, thinly-traded names. A fundamentals overlay on
TODAY'S universe can follow (IBKR snapshot fields 7289/7290/7291, verification
pending Monday's session) — as a forward filter, not a backtested claim.

## The cut (C1), frozen

A signal is eligible only if, on its own signal bar:

    trailing 63-day MEDIAN dollar volume (close × volume) ≥ $25,000,000
    AND close ≥ $5

Median, not mean, so one squeeze day cannot qualify a dead name. Applied
point-in-time inside the harness — a name may be eligible in 2021 and
ineligible in 2015, exactly as it would have been live.

## Questions and gates

**Q1 — momentum on the cut universe.** The full `MOMENTUM_GATES_V2` suite,
same harness, same variant C (pullback + hard −8% + 8% trail), entries
restricted by C1. The gate that matters: **G5 worst single trade ≥ −25%**,
which FAILS at −36.7% uncut. All other gates must also still pass; a cut
that rescues the tail by destroying the mean is a failure, reported as one.

**Q2 — swing on the cut universe.** Same `SWING_OUT_OF_SAMPLE` harness under
C1. Swing has no failing gate; the question is only that the cut does not
damage it (mean within ±0.15% of +0.90%, win rate ≥ 68%).

**Q3 — "already run too deep?"** For every momentum trade (uncut universe,
so the answer is general), record extension at entry = close vs 200-SMA, and
report mean/trade and win rate by extension QUARTILE, cells named now:
Q1 freshest … Q4 most extended. To act on it (as a display verdict or gate),
the spread between best and worst quartile must exceed **0.50%/trade** and be
monotonic-ish; below that it is noise and the verdict is "extension does not
condition returns".

**Q4 — "paper entered yesterday; is it still right to enter today?"** For
both engines, re-simulate every signal with entry at the close 1 and 2 bars
AFTER the signal bar (same exits, run from the delayed entry). Owner's live
workflow follows paper signals with up to a day's lag, so this is the cost of
that lag, measured. Actionable threshold: a delay cost ≤ 0.25%/trade means
"yesterday's signal is still tradeable today"; above 0.50% means it is not.

## Predictions (recorded before the run)

- C1 removes roughly a third to a half of momentum's 41,023 trades.
- **Q1: G5 improves but I put only ~45% on it clearing −25%.** Gaps are not
  exclusive to illiquid names; if the −36.7% print sits in a liquid large
  cap, the cut does nothing to it. Mean/trade holds within ±0.15%.
- Q2: swing barely moves (dip-buying is already concentrated in liquid
  names); all gates still pass.
- Q4: **momentum travels, swing does not.** A trend entered one day late is
  nearly the same trade — I predict momentum's 1-day delay cost ≤ 0.25%. A
  dip entered one day late has already bounced — swing's Q3-confirmation
  study (+0.42% vs +1.00%) says a related 1-bar wait halves the edge, and I
  predict pure 1-day delay costs 0.3–0.6%/trade.
- Q3: **no actionable extension effect** (spread < 0.50%). This week's live
  marks showed winners and losers at identical extension, and the single best
  trade was the most extended name on the board. I expect the intuition to
  fail, and would rather it fail in a 40k-trade table than in the owner's
  conviction.

## Consequences, pre-stated

- Q1 passes all gates → adopt C1 as the universe rule for the momentum lane
  (pre-registered amendment; lane + screen + harvest list all change
  together, every site in one commit). The G5 badge is re-earned honestly.
- Q1 fails G5 still → the tail is not a junk-name artifact; sizing (2%×20)
  remains the only tail control; C1 may still be adopted for operational
  noise reduction but WITHOUT claiming badge repair.
- Q3 actionable → extension becomes a DISPLAY verdict on the momentum board
  (context, like KNIFE on swing), never a silent gate without its own study.
- Q3 not actionable → the board says nothing about extension, and "too
  extended" is retired as a reason to skip a signal.

## Survivorship disclosure

The symbol LIST is today's universe, so names that died before 2026 are
absent — this flatters both engines equally and does not change a
WITHIN-universe comparison (cut vs uncut on the same list), which is what the
gates grade. Absolute levels inherit the same bias the original studies had.

## RESULT — run 3 Oct 2026 (script: backtests/studies/universe_cut_v1.py)

    Q1 MOMENTUM   uncut   n=41,229  win 45.3%  mean +1.42%  worst −36.7%  (G5 FAIL, as published)
                  C1 cut  n=29,982  win 44.5%  mean +1.21%  worst −36.7%  (G1 AND G5 FAIL)

    Q2 SWING      uncut   n=26,773  win 71.0%  mean +0.88%  worst −32.6%
                  C1 cut  n=19,918  win 70.7%  mean +0.86%  worst −32.6%

    Q3 EXTENSION  Q1 freshest +1.54% · Q2 +1.44% · Q3 +1.28% · Q4 most-extended +1.42%
                  spread 0.27% — BELOW the 0.50% actionability bar, and not monotonic

    Q4 DELAY      momentum  d1 −0.01%/trade   d2 −0.04%/trade
                  swing     d1 −0.23%/trade   d2 −0.29%/trade

### Q1 — the cut FAILS, more thoroughly than predicted

The worst trade is **unchanged at −36.7%**: the monster gap lives in a name
that passes any liquidity floor. And the cut is not neutral — it REMOVED
profitable trades: mean falls +1.42% → +1.21% and win rate drops below the
G1 bar. The "junk" names were net contributors. Per the pre-stated
consequence: the tail is NOT a junk-name artifact, sizing (2%×20) remains the
only tail control, and C1 is NOT adopted for momentum — not merely "without
claiming badge repair" but because it measurably costs money.

The owner's operational complaint (too many symbols, too much noise) is real
but it is a PRESENTATION problem — rank and cap what the board SHOWS — not a
signal-generation problem. Deleting signals that make money to quieten a
screen is the wrong trade.

### Q3 — "already run too deep" is RETIRED as a reason to skip

0.27% spread, non-monotonic (the most-extended quartile out-earns two fresher
ones; it wins less often and wins bigger — the rule's published character).
Per the pre-stated consequence the board stays silent about extension.
Prediction: right.

### Q4 — the practical rule for trading paper signals live

**Momentum signals travel. Swing signals decay.**

    momentum: entering 1 day late costs 0.01%/trade — FREE. Two days: 0.04%.
    swing:    entering 1 day late costs 0.23%/trade — a quarter of the edge.

"Paper entered DELL yesterday — can I enter live today?" For momentum: YES,
at essentially no cost. For swing: same-day or accept ~74% of the edge.
Prediction: momentum right (≤0.25%); swing overshot (predicted 0.3–0.6%,
measured 0.23%) — the decay is real but slightly gentler than the
confirmation study implied.

### Predictions, scored
Q1 right to be skeptical (gave G5-pass only 45%; it failed and G1 failed
too, which I did not predict). Q2 right. Q3 right. Q4 momentum right, swing
magnitude overshot. The frozen verdicts stand; no re-runs.

