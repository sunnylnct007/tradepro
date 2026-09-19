# SWING OUT-OF-SAMPLE GATES V1 — does the rule work on names it was never fitted to?

19 Sep 2026. The universe expansion finished yesterday: the bar store now
holds **1,005 symbols**, while every swing study to date has screened the same
**244**. That leaves **761 names the rule has never seen**.

This is the strongest evidence available without waiting for live trades. The
σ-band was *selected* on those 244 (SWING_SIGMA_BAND_GATES_V1 widened 2.5 →
2.25 on them), the hold was *tuned* on them (MEAN_REVERSION_HOLD_V3, 10 → 20),
and the trend floor was *confirmed* on them. Three parameter decisions, one
sample. If the edge is real it should survive on names that had no vote in any
of those choices. If it collapses, we have been reading our own fitting back
to ourselves for a month.

## The test
Run the **live rule, imported not restated** — `SIGMA`, `BB_WINDOW`,
`TREND_WINDOW`, `STOP_PCT`, `MAX_HOLD` from
`tradepro_strategies.signals.mean_reversion` — over the 761 symbols that are
in the bar store but NOT in the traded universe. Entry at the signal close,
exit on target / −8% stop / 20 sessions, exactly as the live sleeve does.

Symbols are screened for tradability first (price and dollar-volume floors
from `universe.py`), because an "edge" that only exists in names too thin to
fill is not an edge. That screen is the SAME one the live universe uses; it is
not a new threshold invented for this study.

## The in-sample result this is measured against
n=1660 · win 70.4% · **+0.87%/trade** · worst −19.6%

## Gates — the rule GENERALISES only if all four hold
- **G0** n ≥ 1,000 out-of-sample trades with usable price history
- **G1** mean return ≥ **+0.40%/trade** — half the in-sample edge. Anything
  above this is a real effect that decayed; below it, the in-sample number was
  mostly selection
- **G2** win rate ≥ **60%** (in-sample 70.4%)
- **G3** worst single trade ≥ **−25%**, the same tail bar the band study used
- **G4** mean is positive in BOTH halves of the period, so one regime cannot
  carry it

## Prediction (recorded before the run)
**G1 passes, but the edge decays to roughly +0.5 to +0.7%/trade.** I give
generalisation ~65%.

Reasoning: the rule is deliberately crude — three parameters, all chosen by
pre-registered study rather than search — so there is little surface to overfit.
But the 761 are systematically *different*, not merely new: they are what was
left after the traded universe took the large, liquid names, so they skew
smaller and more volatile. Mean reversion usually looks BETTER on such names
(bigger dips) and fills WORSE. Since this study enters at the close and does
not model spread, I expect the raw number to flatter them, which is why G1 is
set at half rather than at parity.

**The outcome I would find most informative is G2 holding while G1 fails** —
the dips still revert, but by less per trade, which would say the effect is
real and the SIZE of it was fitted.

## Consequences, pre-stated
- **All four pass** → the rule generalises. The universe should be expanded
  and the traded name-list widened, which is the difference between ~7 signals
  a week and a usable cadence.
- **G1 fails, G2 holds** → the direction is real, the magnitude was fitted.
  Keep the rule, stop quoting +0.87%/trade as its expectation, and re-derive
  the number from the combined sample.
- **G2 or G4 fails** → the edge does not survive contact with unseen names.
  The swing sleeve stays at 244 names and stops being described as validated.
- **G3 fails** → the tail is worse than the stop implies on smaller names;
  the sleeve must not be widened to them whatever the mean says.

Nothing here changes the live rule. This measures whether it travels.

## RESULT — run 19 Sep 2026. The edge travels. The TAIL was never what we said.

745 unseen symbols, **21,948 out-of-sample trades**, 2006 → 2026.

| | n | win | mean/trade | median | worst |
|---|---|---|---|---|---|
| **out of sample** (unseen) | 21,948 | **71.3%** | **+0.90%** | +2.24% | −32.5% |
| in sample (the traded 244) | 5,416 | 69.8% | +0.81% | +1.92% | −32.6% |

- **G0 PASS** (21,948) · **G1 PASS** (+0.90%) · **G2 PASS** (71.3%) ·
  **G4 PASS** (+0.93% early / +0.86% late) · **G3 FAIL** (−32.5% vs −25% bar)

Formally: **DOES NOT GENERALISE**, because G3 was pre-registered and G3 failed.
That verdict stands as written. But it is not what the run actually found, and
the honest reading matters more than the label.

### The edge generalises completely — better than in-sample
There is **no decay**: +0.90% out-of-sample against +0.81% in-sample, on names
that had no vote in choosing σ, the hold, or the trend floor. Win rate is
higher too. My prediction (decay to +0.5–0.7%, ~65% confidence) was **wrong in
the direction that matters**: I assumed three parameter decisions on one sample
had bought us something, and they had not. The rule is crude enough to travel.

### G3 failed on BOTH samples, which means it was not testing generalisation
In-sample worst is −32.6%, out-of-sample −32.5%. A gate that fires identically
on both is measuring the harness or the record, not the new names. It was the
latter. The five worst trades in the traded universe are:

    DIA   2007-10-19  −32.6%
    COP   2020-02-28  −29.9%   (COVID)
    HYG   2021-11-26  −23.2%
    RKLB  2023-09-12  −19.6%   ← quoted as "worst" in SWING_SIGMA_BAND_GATES_V1
    HOOD  2024-08-02  −17.7%   ← quoted as "worst" in mean_reversion_swing.py

**Our published tail figures were the FOURTH and FIFTH worst trades.** The
three worse ones were invisible because those symbols had no history: 98 of
244 names were capped at roughly four years by IBKR's 1000-bar limit
([[project_swing_gates_reproducibility]]). Backfilling 110 symbols from ~530
bars to 5,208 on 18 Sep pulled 2007 and the COVID crash into range, and the
tail appeared. **Nothing about the rule changed. We can simply see it now.**

So G3 did its job, just not the job it was written for: it caught that the
number we have been quoting for a month is an artefact of missing data.

### What this changes
1. **The −8% stop does not cap the loss.** Three trades lost 23–33% against
   it. It is checked on the close and gaps blow straight through — the file
   already says so; what was missing was how far.
2. **Position sizing on the LIVE sleeve is calibrated to a tail that is half
   the real one.** This is the finding with money attached: the book is funded
   at $150k, and the worst case per trade is roughly twice what the record
   implied.
3. **Widening the universe is supported on return and blocked on risk.** The
   745 unseen names show the same edge and the same tail. Expanding the sleeve
   should follow a sizing decision, not precede it.
4. Both published tail figures should be corrected wherever they appear.

### What it does NOT change
The entry rule. σ=2.25, the 200-day floor and the 20-session hold are all
re-confirmed on five times more data than they were chosen on.
