# Swing earnings-veto — RESULT, graded 5 Sep 2026

Gates: `SWING_EARNINGS_VETO_GATES_V1.md` @ `6a57eb0`, committed before the
cells were computed. Thresholds not moved. **Both vetoes REJECTED. Swing
stays exactly as it is.**

## V-PAST — the veto the aggregate begged for: NOT robust

| Gate | Result | |
|---|---|---|
| VP1 share ≤20% | 10.9% (251 of 2,304) | PASS |
| VP2 edge ≥ +0.30pt (pro forma, seen) | +0.70pt | PASS |
| VP3 all four cells | **early/A: vetoed +2.05% BEAT kept +1.65%** | FAIL |

The −0.70pt aggregate deficit is real and is **carried almost entirely by one
cell**: late/A, where earnings-driven entries lost −1.20% against +0.93% kept.
In early/A the vetoed trades were the BETTER ones. An effect that lives in one
quadrant of (time × universe) is a pocket, not a rule — this is precisely the
pattern the two-split exists to catch, and it caught it in the same week it
killed Q3. Vetoing on this evidence would fit one stretch of history.

## V-FUTURE — the direction is INVERTED

| Gate | Result | |
|---|---|---|
| VF1 n ≥ 100 | 438 | PASS |
| VF2 edge ≥ +0.30pt | **−0.59pt — vetoed trades did BETTER** (+1.83% vs +1.23%) | FAIL |
| VF3 all four cells | vetoed better in 3 of 4 | FAIL |

Swing entries with a print inside the coming 10 sessions outperformed. The
Q3 tail fear (−37.6% holding through a print) does not translate into this
population: a dip that qualifies for swing AND has a print ahead has, on this
history, resolved upward more often than not. Vetoing them would have removed
438 above-average trades.

## Predictions vs outcome — both wrong, usefully

* V-PAST: predicted ships at ~70%. **Did not ship.** The unseen cells did
  exactly the job pre-registration reserves for them.
* V-FUTURE: predicted a deficit failing one cell. **There is no deficit at
  all** — the sign is inverted.

## Disposition

No change to `mean_reversion_swing`. No config keys created. The earnings
workstream closes with zero shipped strategy changes and three answered
questions: the pre-print entry has no edge over a plain dip (Q3), the
post-print drop is worse than the rule it would replace (Q2), and neither
earnings veto survives its cells (this doc). What remains is the durable
asset: 11 years of report dates and `tradepro-earnings-backtest`, a disk-only
grader any future earnings claim must pass through.
