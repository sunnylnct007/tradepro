# Strangle vol gate — pricing the crisis leak

**Pre-registered 2026-10-10, BEFORE the study ran.** Thresholds, gates and the
prediction below were committed first. Results are appended verbatim, including
a wrong prediction.

## The question

The gate has never opened. Over the 30 sessions the desk has been live
(31 Aug → 9 Oct 2026) XSP's gate is `VIX <= 13.5` and VIX's **minimum was
14.32**: 0 of 30 sessions. Across 476 decisions in 8 markets, zero placements.

That is not a bug. `choose_threshold` picks the **largest threshold on a
half-point grid that leaks ZERO sessions** from GFC, COVID, the 2022 bear and
April 2025. For VIX the cliff is one half-point wide:

    13.5 -> 1,988 sessions   CLEAN      <- chosen
    14.0 -> 2,318 sessions   COVID:1

So the rule is a crisis-avoidance constraint, and it is **unpriced**: "zero
leakage" treats every crisis session as infinitely costly. Against the current
regime that absolute costs the entire strategy.

**The question this study answers: what does a leaked crisis session actually
cost?** Once that is a number, the gate stops being "largest with zero" and
becomes "largest whose tail stays inside a budget the owner set" — which is a
decision the owner can actually take.

This study does NOT propose a threshold. It prices the leak. Changing the gate
means changing the RULE, and `test_thresholds_are_the_rules_output` fails if
any threshold is hand-edited — deliberately, because the owner asked "how did
you decide on the threshold value" and the answer must stay "the rule computed
it".

## Method

Per-trade returns come from the EXISTING `trade_returns` in
`index_strangle_sim.py` — not a new model. One trade = sell the strangle at the
open, buy it back at the close, same day, with the vol index **lagged one
session** (the gate may only see the previous close; un-lagging it quietly
excludes the very days it is meant to avoid). Returns are % of collateral.

For each threshold `t` in 13.0 … 18.0 (half-points — the decision-relevant
range; the full grid is in `choose_threshold`):

- `n` — sessions the gate would allow, over full history
- `mean` — mean per-trade return, % of collateral
- `live` — how many of the 30 live sessions it would have allowed
- **leaked** — sessions inside a crisis window, counted AND priced:
  - `leak_n`, `leak_sum` (aggregate % of collateral), `leak_worst` (single
    worst leaked session)

Markets: XSP, SPX, SPY, NDX, QQQ, GOLD. XSP is the decision market — it is the
only unit `PLACE_UNITS` permits to place.

## Gates

- **G1 — the premise.** Leaked crisis sessions must be LOSSES on average
  (`leak_sum < 0` wherever `leak_n > 0`). If crisis sessions are *profitable*
  on average, "zero leakage" is protecting against nothing and the rule should
  be rethought, not retuned. G1 failing invalidates the whole framing.
- **G2 — reachability at a priced tail.** There must exist a `t >= 15.0`
  (≥30% of live sessions, i.e. genuinely tradeable in this regime) whose
  `leak_worst >= -10.0` — no single leaked session losing more than 10% of
  collateral. If no such `t` exists, the gate **cannot** be widened into this
  regime on tail grounds, and the strangle stays shut until the regime changes.
- **G3 — widening must not be strictly worse.** `mean` must not fall
  monotonically across 13.5 → 17.0. If looser gates earn less per trade AND
  leak more, widening is dominated and the study ends there.

## Kill condition (K1)

If the only way a threshold passes G2 is by excluding, shortening or
reweighting any of the four crisis windows, the result is **REJECTED**. The
windows were fixed before this study and are not an output of it. This exists
because an amendment written after seeing tempting data has curve-fit this desk
before.

## Prediction — written before running

1. **G1 PASSES.** Crisis sessions are losses for a short-vol trade.
2. **G3 PASSES**, and `mean` will be roughly FLAT across thresholds.
   `index_strangle_paper.py:77` already states "the mean barely moves across
   thresholds; the TAIL does" — this study should reproduce that.
3. **G2 FAILS.** I expect the worst leaked session at `t >= 15.0` to lose well
   more than 10% of collateral, because by 15.0 both COVID and April 2025 leak
   multiple sessions and an un-gated short strangle in those windows loses a
   large multiple of its credit. If that is right, the honest answer to "where
   should the gate be" is **the gate is correct and the regime is wrong** — the
   strategy is unreachable until VIX returns to sub-14, and no amount of
   retuning fixes that without buying a tail the desk has not agreed to.

If 3 is right, this study ends with no change to the gate, and the useful
output is the GOLD and NDX finding below rather than a new VIX threshold.

## Separately established, not part of the gates

Two gates are not merely binding, they are **unreachable in any regime
resembling this one** — stated here because the study will confirm or refute it
in passing:

- **GOLD**: gate `GVZ <= 11.5`, GVZ minimum over the live window **22.44** —
  wrong by roughly 2x. Only 347 sessions in all history ever qualified.
- **NDX/QQQ**: gate `VXN <= 17.5`, VXN minimum **19.29**.

## Results

*(appended after the run — see below)*
