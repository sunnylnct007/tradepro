# Equity trend filter V1 — RESULT

Gates and prediction committed in `db33fb7` before the run. Result appended
verbatim, including the parts I got wrong.

## Numbers

| | CAGR | max drawdown |
|---|---|---|
| **200-day trend filter** | **18.95%** | **−30.33%** |
| Control A — same 100 names, buy & hold | 22.05% | −32.26% |
| Control B — SPY | 13.94% | −33.72% |

98 names × 2,750 sessions, 2015-12-01 → 2026-11-06. 5bps per side.

| gate | measure | result | bar | |
|---|---|---|---|---|
| G1 | CAGR − Control A | **−3.10 pt** | ≥ +1.0 | **FAIL** |
| G2 | CAGR − SPY | +5.01 pt | ≥ 0.0 | PASS |
| G3 | maxDD vs Control A | +1.93 pt | ≤ Control A | PASS |
| G4 | 2022 vs Control A | +5.87 pt | ≥ +5.0 | PASS |
| G5 | 2020 maxDD | **−30.33%** | ≥ −30% | **FAIL** |
| G6 | turnover/name/yr | 0.02 | ≤ 6 | PASS |

**VERDICT: FAILED.** The document said G1–G6 all passing is what closes this.
Two failed.

## The prediction, graded

Every call in the pre-registration held:

- *"G2 probably PASSES"* — it did, +5.01.
- *"G1 is where this is decided, and I predict it FAILS ... trails Control A
  by 1–3 points"* — **failed by 3.10**, just outside the stated range.
- *"G3 probably PASSES, and that is the tension: lower return, lower
  drawdown"* — exactly what happened.
- *"G4 should pass"* — +5.87.
- *"G5 is the one I am least sure of: March 2020 fell too fast for a 200-day
  filter to help"* — **failed, by 0.33 of a point.**

## What it means

**The timing rule subtracted value. The universe added it.**

The filter beat SPY by 5 points — which reads like success until Control A is
in the room. Buy-and-hold of the *same 100 names* beat SPY by 8.1 points, and
beat the filter by 3.1. So the outperformance came from **owning large, liquid,
quality companies**, and the 200-day rule gave a third of it away by selling
dips and buying them back higher.

This is the same finding as `ENTRY_EDGE_GATES_V1`, reached from the other
direction: our timing keeps costing money on names that work.

**What the rule IS good at** is the thing it was supposed to be: −1.93 points
of drawdown and +5.87 in 2022. That is a RISK CONTROL, not an edge — the same
category as the options structure routing, which also does not make money and
also bounds what can be lost.

G5 is worth naming honestly. It failed by a third of a point, and a 30% bar
was my choice. It would be easy to call that "essentially a pass" — but the
bar was set before the run precisely so it could not be renegotiated after it.

## Decision

Per the pre-registration: **the equity lane is an ownership decision, not a
trading one.** Owning the derived 100-name sleeve and doing nothing beat every
timed version of it we have tested, twice now, by two different methods.

The next equity question worth asking is therefore NOT "what is a better
entry signal". It is **"what belongs in the sleeve"** — because selection is
where all the measured value has come from.

No V2 with a different lookback. A 150-day or 250-day filter would be fitting
a parameter to a conclusion we already have.
