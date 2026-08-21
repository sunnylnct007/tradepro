# ICH exit-rule study — PRE-REGISTERED gates

**Committed BEFORE the first run** (21 Aug 2026). Protocol as per
`WHEEL_BACKTEST_GATES_V2/V3.md`, `SR_LEVEL_STUDY_GATES_V1.md` and
`ICH_SR_FILTER_GATES_V1.md`.

## Why exits, and why not entries

Three entry ideas have now been tested and all three failed or actively hurt:

- support-distance filter — graded, FAILED (G1 +1.9pts where +4 needed)
- extension / 200-SMA / RSI / kijun-distance gates (the ICH_IBKR stack) —
  measured, total return **17,148% → 9,832%**, a 43% loss of profit, with win
  rate slightly WORSE (39.1% → 38.3%)
- conviction ranking — real but weak (Q1 +1.22% vs Q4 +2.37%, win rate flat)

The distribution says why. **The top 1% of trades carry 55% of all profit**;
six of ten trades lose. Under that shape every entry filter is a lottery-ticket
shredder — it removes chances at the tail without improving ticket quality, and
the top-1% share RISES to 61% as gates are added, i.e. the strategy becomes more
fragile, not less.

Meanwhile hold length dominates everything:

    0-2 bars    n=1,101   mean  -2.16%   win 17%
    3-5         n=1,104   mean  -4.09%   win  6%
    6-10        n=1,245   mean  -4.80%   win  3%
    11-20       n=1,557   mean  -4.32%   win  9%
    21-40       n=2,582   mean  +0.81%   win 51%
    41+         n=2,134   mean +16.24%   win 96%

Every short hold loses; every long hold wins. Live median hold is 3 days
(T212) and 0 days (IBKR). **The strategy never reaches the only rows that pay.**

## The candidate mechanism

The spec's exit is `Close < cloud_bottom OR tenkan < kijun`. Tenkan is a
5-period line, kijun 32. A 5-period line crosses below a 32-period line on any
minor pullback — long before the trend is actually broken. The hypothesis is
that **the TK leg of the exit is what cuts winners**, and the cloud_bottom leg
alone is the real trend break.

## Variants under test

| # | Exit rule | Note |
|---|---|---|
| A | `Close<cloud_bottom OR tenkan<kijun` | the SPEC, baseline |
| B | `Close<cloud_bottom` only | drops the fast TK leg — **DEPARTS FROM SPEC** |
| C | A, but no exit before 20 bars unless `Close<cloud_bottom` | min-hold |
| D | `Close < kijun` | slower trail than tenkan, faster than cloud |

**B and D are deliberate departures from the trader's documented spec** and are
labelled as such. Under the standing verbatim-port rule they may be RESEARCHED
but must never silently replace the live signal; adopting one would require the
owner's explicit decision and a new parity test.

Entries are IDENTICAL across all four (plain spec, no gates) so the only thing
varying is the exit. Costs 5bps/side, MOO fills, same universe and window.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Trades per variant (validity) | ≥ 1,000 |
| **G1** | Best variant's TOTAL return ≥ baseline A × 1.30 | true |
| **G2** | Best variant's mean/trade ≥ baseline × 1.20 | true |
| **G3** | Best variant's median hold ≥ 21 bars | true |
| **G4** | Top-1% profit share does NOT rise vs baseline | true |

**G4 is the anti-fragility gate.** Improving total return by concentrating even
harder into a handful of outliers is not an improvement — it is the same trap
the entry filters fell into, and it must be checked rather than assumed.

**G3 targets the live failure directly.** A rule that improves returns without
lengthening the hold has not addressed why the live sleeves lose money.

## Prediction — recorded before the work

**B (cloud-only) passes G1/G2/G3; G4 is the risk.** Reasoning: removing the
fast TK exit should keep positions through pullbacks and push median hold well
past 21 bars, and the hold table says that is where the money is. But it will
also hold losers longer, so mean-per-trade may improve less than total return,
and the tail share could worsen — which is exactly what G4 exists to catch.

**D (kijun trail) I expect to land between A and B.**

If no variant clears G1, the answer is that ICH's exit is already reasonable
and the live problem is purely the premature-exit BUG, not the rule.
