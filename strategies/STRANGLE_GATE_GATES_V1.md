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

## Results — run 2026-10-10

### G1 — FAILED. The premise is wrong.

Leaked crisis sessions are not losses. Priced as the harness ships them, every
threshold from 14.0 to 18.0 leaks crisis sessions whose **aggregate return is
POSITIVE** and whose worst single session is `+0.05%` to `-0.38%` of
collateral. At `t = 15.0` the worst leaked session is `-0.01%`.

The reason is not subtle once seen: the gate selects on the PREVIOUS close's
vol index, and **a session's membership of a crisis window says nothing about
its entry vol.** Early-February 2020 sits inside the COVID window at VIX 14 —
calendar overlap, not danger. The eight genuinely bad sessions in XSP's whole
history all had entry VIX between **23 and 70**, so no threshold under
consideration (13.5–18.0) would ever have traded them.

So "zero crisis leakage" is counting calendar coincidence and treating it as
risk. It is not protecting against the thing it appears to protect against.

### The harness could not see the main risk — and that is the real finding

`trade_returns` prices the exit with the **entry's** implied vol: the same `iv`
on both legs of the round trip (`index_strangle_sim.py:186-194`). A short
strangle's dominant crisis risk is vol EXPANSION, and that is structurally
invisible to it. Repricing only the exit leg at the day's own vol close, at the
**monthly tenor `PLACE_UNITS` actually permits** (DTE 21):

    thr       n    mean flat   mean real    worst real   crisis worst
    13.5   1988        0.019       0.001         -2.33            n/a   <- live gate
    15.0   2965        0.020       0.004         -2.33          -0.15
    17.5   4592        0.020       0.004         -3.95          -1.38

**The edge is 0.019% of collateral as shipped and 0.001% once vol expansion is
priced** — a ~95% haircut, before commission and slippage. It is flat across
every threshold, so widening the gate does not buy return, because at this
tenor there is no measured return to buy.

The weekly tenor survives the same test far better: `0.029% -> 0.021%`, a ~25%
haircut, still clearly positive. **`PLACE_UNITS` permits `monthly` only** — the
one tenor whose edge does not survive being priced honestly.

### The worst row in the study

    2018-02-05   VIX(entry) 17.31   flat -0.83%   repriced -3.95%

Volmageddon. Entry vol **below 17.5**, so a gate at 17.5 would have TRADED it.
The shipped harness prices it at `-0.83%`; priced honestly it is `-3.95%`, the
worst single session in the set. **It is in none of the four crisis windows.**
A rule that selects thresholds by crisis-window leakage cannot see it.

### G2 — passes, and is moot

At `t = 15.0` (30% of live sessions) `leak_worst` is `-0.15%`, far inside the
`-10%` budget. G2 passes trivially — but on an accounting G1 just invalidated,
so it carries no weight.

### G3 — passes as predicted, but the level is the problem

`mean` is flat across 13.5 → 18.0, exactly as `index_strangle_paper.py:77`
says. Prediction 2 correct. The flatness is no longer reassuring: the mean is
flat at approximately zero.

### K1 — not triggered

No crisis window was excluded, shortened or reweighted. G1 fails on a finding
ABOUT what the windows measure, not on an adjustment to them.

### Prediction scorecard

| # | Prediction | Outcome |
|---|---|---|
| 1 | G1 passes — crisis sessions are losses | **WRONG.** They are profitable; the windows do not select dangerous days |
| 2 | G3 passes, mean flat across thresholds | **RIGHT** |
| 3 | G2 fails — gate correct, regime wrong | **WRONG**, and wrong twice over: G2 passes easily, and "where should the gate be" turned out to be the wrong question |

Two of three wrong. The conclusion I expected to reach — "the gate is right,
the regime is wrong, leave it alone" — is not what the numbers say. They say
the gate is selected by a criterion that does not measure risk, guarding a
monthly trade whose edge does not survive honest pricing.

## Conclusion — and what NOT to do

**Do not widen the gate.** Not because the tail forbids it, but because
widening buys more of a trade with no demonstrated edge at the permitted tenor.
Raising 13.5 would have produced placements, a filled board, and the appearance
of a working strategy — on an edge of 0.001% of collateral.

The three things this establishes, in priority order:

1. **The monthly tenor has no edge once vol expansion is priced.** This is the
   finding that matters. Everything else is downstream.
2. **`choose_threshold` selects on a criterion that does not measure risk.**
   Crisis-window leakage is calendar overlap. Feb 2018 — the worst session in
   the study, and tradeable at 17.5 — is invisible to it.
3. **`trade_returns` holds IV constant across the round trip.** Every number
   this desk has recorded about the strangle inherits that. The shadow book's
   `-$1,985` over 25 pairs is, if anything, the honest signal and the harness
   was the optimistic one.

Open owner decisions, NOT taken here:

- Whether to reprice `trade_returns` with a close-vol exit. It changes every
  historical strangle number on the desk, so it is a decision, not a cleanup.
- Whether to switch `PLACE_UNITS` to `weekly`, whose edge survives repricing.
  That is a widening, and widenings on this desk follow a clean run.
- GOLD (`GVZ <= 11.5` vs a live minimum of 22.44) and NDX/QQQ
  (`VXN <= 17.5` vs 19.29) remain unreachable. Confirmed in passing, as the
  pre-registration said it would be.
