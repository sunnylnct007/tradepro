# ICH exit study v2 — PROFIT TARGETS, with win rate as a HARD gate

**Committed BEFORE the run** (22 Aug 2026). Follows ICH_EXIT_GATES_V1.md.

## Why v2

v1 found that removing the fast `tenkan<kijun` exit leg raises total return 41%
and median hold 19→34 bars. But it drops the win rate to 34.7%, and the owner's
constraint is explicit: **"a winrate of 34-35% is a no-go for an algo platform."**

That is a legitimate design constraint, so it becomes a GATE rather than a
caveat. v1's variant C (min-hold 20 bars) already beats the current spec on win
rate (41.8% vs 39.1%) AND total return — this run asks whether profit targets
can push the win rate higher without gutting the tail that carries the profit.

## The tension being tested

A profit target mechanically RAISES win rate (more trades close green) and
mechanically CAPS the tail (the top 1% of trades currently carry 55% of all
profit). Those pull in opposite directions. The question is whether any
combination lands above the win-rate floor while still beating the baseline's
total return. It is entirely possible that none does — that is a valid answer.

## Variants

Entries identical throughout (plain spec, no gates). Only exits vary.
Costs 5bps/side, MOO fills, same universe/window as v1.

| # | Rule |
|---|------|
| A | spec baseline: `close<cloud_bottom OR tenkan<kijun` |
| C | v1 winner: A, but ignore the TK leg before 20 bars |
| E | A + take-profit at +10% |
| F | A + take-profit at +20% |
| G | C + take-profit at +20% |
| H | scale-out: half out at +15%, remainder runs to A's exit |

H is the classic answer to this exact tension and is the one I would expect an
algo desk to actually run.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Trades (validity) | ≥ 1,000 |
| **G1** | **Win rate ≥ 45%** — the owner's constraint, HARD | true |
| **G2** | Total return ≥ baseline A | true |
| **G3** | Median hold ≥ 21 bars | true |
| **G4** | Top-1% profit share ≤ baseline A | true |

**G1 is now a hard floor, not a preference.** A variant that fails G1 is
rejected regardless of how much money it makes.

**G2 is deliberately only "≥ baseline", not a multiple.** If a target raises
the win rate to a workable level while merely matching total return, that is a
GOOD trade for a platform that has to be run by a human every day.

## Prediction — recorded before the work

**H (scale-out) is the most likely to pass all four.** Taking half off at +15%
converts a chunk of the current losers-that-were-briefly-winners into wins,
while the remaining half preserves tail participation.

**E (+10% target) will likely pass G1 and FAIL G2** — a 10% cap on a strategy
whose top 1% of trades carry 55% of profit should cut too much of the tail.

If nothing clears all four, the honest conclusion is that this signal family
cannot deliver a ≥45% win rate, and the choice becomes accepting ~40% or
changing signal family entirely.
