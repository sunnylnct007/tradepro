# Earnings v2 — RESULT, graded 5 Sep 2026

Gates: `EARNINGS_GATES_V2.md` @ `16de3c0`, committed before the runner existed.
Artifact: `backtests/earnings_v2_result.json`. Thresholds were not moved.

## Q3 — buy weakness into the print: **FAILS, 6 of 8 gates. Does not ship.**

539 events · 182 symbols · AMC-conservative alignment · costs 10 bps/side

| Gate | Bar | Result | |
|---|---|---|---|
| P0 sample | ≥400 / ≥80 syms | 539 / 182 | PASS |
| P1 mean/trade | ≥ +0.75% | **+0.42%** | FAIL |
| P2 edge vs no-print dip | ≥ +0.50pt | **+0.11pt** (arm +0.31%, n=18,749) | FAIL |
| P3 win rate | ≥ 55% | **49.4%** | FAIL |
| P4 5th percentile | ≥ −12% | **−12.85%** | FAIL |
| P5 worst trade | ≥ −35% | **−37.62%** | FAIL |
| P6 two-split | all 4 cells > 0 | late/A-half **−0.19%** | FAIL |
| A3 alignment ±1 | both > 0 | +0.65% / +0.26% | PASS |

**The sentence that matters: the print adds +0.11pt over an identical dip with
no print.** Generic dip-buying (which this desk already owns) earned +0.31% on
18,749 occurrences; adding "and earnings are tomorrow" moved it to +0.42% and
bought a coin-flip win rate and a −37.6% worst case for the privilege. A3
passing means this is not an alignment artefact — the smallness is real.

The owner's DELL and SNOW wins sit inside this distribution: two good draws
from a pool averaging +0.42% with 49% winners. They were good trades. They
were not evidence of a system, and now that is measured rather than suspected.

## Q1 — v1's prediction CONFIRMED: earnings drops bounce WORSE

| Arm | n | mean/trade |
|---|---|---|
| live rule, earnings-driven (print ≤3 sessions before signal) | 251 | **+0.72%** |
| live rule, everything else | 2,053 | **+1.42%** |

The 28 Aug prediction — *"an earnings drop is information; a no-news 2.5σ drop
is noise, and noise is what mean reversion feeds on"* — is now evidence.
**Practical follow-up worth its own small pre-registration: an earnings-window
VETO on swing entries.** The rule currently has zero earnings awareness; 251 of
its 2,304 historical trades were earnings-driven at half the edge of the rest.

## Q2 — earnings-session drop, swing exits: better than Q3, still loses to the rule

327 trades · win 59.0% · mean +0.51% · worst −16.13% — clears V0/E1/E2/E5,
**fails E3** (does not beat the live rule's +1.42% by ≥0.20pt — it is 0.91pt
WORSE). E4 (two-split) was not graded for Q2 by this runner; with E3 already
failed the verdict does not depend on it. Q2 finds more trades of lower
quality. Does not ship.

## Coverage, stated

8,326 raw events → 8,321 clustered; **2,222 dropped for missing bar history**
(median bar start 2019-08-30 — the store's ~6–7y median, not the calendar's
11y). The pre-2020 split half exists but is thin. Every dropped event is in
the artifact's coverage block.

## Prediction vs outcome

Predicted: P1 marginal pass, P2 fails a cell, <40% ships. Outcome: does not
ship (right), but WEAKER than predicted — P1 failed outright and the win rate
was a coin flip. The direction of the error is the safe one, and the gates,
not the prediction, did the deciding.
