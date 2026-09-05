# Swing earnings-veto — PRE-REGISTERED gates, v1

**Committed BEFORE the veto-specific grading**, 5 Sep 2026. Follows the
earnings v2 result (`3a911b9`): the live swing rule's earnings-driven entries
earned +0.72%/trade against +1.42% for everything else (251 vs 2,053 trades).

## What is already known, and therefore proves nothing

The aggregate deficit (−0.70pt) is the OBSERVATION THAT MOTIVATED THIS DOC.
It has been seen. Grading it again would be prediction after the fact, so it
appears below only as a pro-forma check (VP2) — **the real content of this
pre-registration is what has NOT been computed yet**: the per-cell robustness
of the deficit (VP3) and the entire forward-looking question (VF).

## The two vetoes under test

**V-PAST** — refuse a swing entry when a known print occurred in the 3
sessions up to and including the signal bar. Exactly the Q1 tag; the entry is
a reaction to earnings information, which mean reversion mistakes for noise.

**V-FUTURE** — refuse a swing entry when a known print is scheduled within
the 10 sessions after the signal bar (median hold is 9). This is untested by
Q1 entirely: the hazard is not the entry but the HOLD — Q3 measured a −37.6%
worst case for positions carried through a print. Live, this uses only
knowable information: the forward calendar as of the signal date.

## Gates

| # | Test | Threshold |
|---|------|-----------|
| VP1 | Vetoed share of the rule's trades | ≤ 20% (sample must survive the veto) |
| VP2 | Kept minus vetoed, mean/trade (pro forma — already seen) | ≥ +0.30pt |
| VP3 | Kept beats vetoed in the two-split | ALL FOUR cells (time × symbol) |
| VF1 | Future-print entries found | ≥ 100 (else VF is UNGRADEABLE, not passed) |
| VF2 | Kept minus future-print, mean/trade | ≥ +0.30pt |
| VF3 | Kept beats future-print in the two-split | ALL FOUR cells |

V-PAST ships iff VP1–VP3 all pass. V-FUTURE ships iff VF1–VF3 all pass.
They are graded independently; either can ship alone.

## Implementation contract, decided now so a pass cannot re-open it

* Config-driven: `settings-kv swing_earnings_veto_past_sessions` (default 3)
  and `swing_earnings_veto_future_sessions` (default 10); 0 disables either.
* **Feed-down behaviour: veto NOTHING, loudly.** If the earnings calendar is
  unreachable at decision time, the rule trades as it always has and the
  decision log says the veto was not applied. The ICH NaN incident is what a
  silent fail-closed veto does — nine days of every entry vetoed by a missing
  number. A veto is an improvement, never a new way to stop trading.
* The veto is logged per decision with the print date that triggered it, so
  forward-test F2 can trace every non-trade the same way it traces trades.

## Predictions — recorded before the cells are computed

* **VP3: passes.** 251 trades across 4 cells is ~60 each; the deficit is large
  enough that I expect all four to hold. ~70% confident V-PAST ships.
* **VF: the deficit exists but VF3 fails at least one cell.** Future-print
  entries should be rarer and noisier, and their harm concentrated in tail
  events that land in whichever cell contains them. ~35% confident V-FUTURE
  ships as specified.

**Thresholds do not move after the numbers are seen.** A VP3 cell at −0.01pt
is a fail, and the write-up will say so.
