# Resting limit at the trigger level — pre-registered

**Committed BEFORE the run.** 24 Aug 2026.

## The question

Owner: *"if that's the case why can't I book an order placed at that price."*

The shipped rule waits for a settled CLOSE below 2.5σ, then buys the next open.
A resting limit at the 2.5σ level fills the moment price TOUCHES it intraday —
which happens far more often, because most touches revert before the close.

An exploratory pass (each arm over every bar independently) gave:

| | trades | win% | per trade | worst |
|---|---|---|---|---|
| the rule — close below, buy next open | 2,501 | 72.9% | +0.96% | -23.5% |
| resting limit, fill on touch | **7,666** | 73.4% | **+1.01%** | **-28.1%** |

Three times the trades at a slightly better per-trade return. **And it breaks
G5** — worst -28.1% against a -25% ceiling. That is the whole problem: the
limit fills into names that touch the level and keep falling, which the close
filter rejects.

So the question is not "is the limit better" — exploratory says yes on
expectancy. It is **can the tail be brought back inside the gates without
giving away the extra trades.**

## Variants (declared in advance)

All rest a limit at `20-day mean − 2.5σ`, filled on touch, trend filter
(above the 200-SMA) applied at the signal bar as now.

* **A — control**: the shipped rule. Close below, buy next open.
* **B — naive limit**: fill on touch, -8% stop, 20-session cap. The version
  measured above.
* **C — limit + tighter stop**: -6%. Cuts the tail, costs some winners.
* **D — limit + close confirmation**: fill on touch, but EXIT next open if the
  session does not also close below the level. Keeps the early fill, drops the
  ones that were only a wick.
* **E — limit + trend re-check**: fill on touch only if the symbol is still
  above its 200-SMA *on that bar*, not merely at the signal.

## Gates

A variant ships only if it passes **every** gate.

| # | Gate | Threshold | Why |
|---|---|---|---|
| R1 | Trades | >= 2,501 (the control's count) | The entire appeal is more opportunities. A variant that ends up with fewer has no reason to exist. |
| R2 | Mean per trade | >= +0.96% (the control) | Must not pay for the extra trades with a worse average. |
| R3 | **Worst trade** | **>= -25%** | The gate the naive version FAILS at -28.1%. This is the one that decides it. |
| R4 | Top-1% share of net | <= 25% | Same tail-concentration gate the strategy already carries. |
| R5 | Median hold | <= 20 sessions | Same cap. |
| R6 | **Survives both splits** | R2 and R3 hold in all 4 cells | Time split AND symbol split — the test that killed momentum v3, the dip study, and would have caught both on the full sample. |

## Prediction, on record

1. **B fails R3** — it already does at -28.1%, and nothing about running it
   again changes that.
2. **C (tighter stop) passes R3 but I expect it to fail R2**: a -6% stop on a
   mean-reversion trade cuts winners that were going to come back. This
   project measured that once already — wider stops beat tighter ones in every
   row of the R:R sweep, because the strategy needs to survive the drawdown.
3. **D (close confirmation) is my pick to ship**, and the reasoning is that it
   removes exactly the trades that cause the tail — the wicks that never
   closed below the level — while keeping the early fill on the ones that did.
4. **R6 is where any survivor most likely dies**, as it has three times this
   month.
5. Overall: **~40%** that anything passes all six.

If nothing passes, the rule stays as it is and this file is kept with the
other failures.
