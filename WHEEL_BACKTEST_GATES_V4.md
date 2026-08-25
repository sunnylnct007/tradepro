# Wheel v4 — a STOP on the assigned shares. Pre-registered.

**Written BEFORE the run, 25 Aug 2026.** Owner: *"we do need the wheel strategy
working if you have time."*

## Why v3 failed, in the harness's own words

    "Entry filters cannot reach the failure mechanism, which is being assigned
     into a decliner and holding it."

v3 added a 200-SMA trend floor specifically to rescue it. It did not:

| gate | test | v3 result | |
|---|---|---|---|
| G3 | full-period net CAGR ≥ 8%/yr | 7.61% (v2: 8.25%) | FAIL |
| G4 | worst single-symbol drawdown ≤ 40% | META −71.4% | FAIL |

And v2's celebrated "8/9 PASS" was flattered — the harness had never graded G4
on the full window, so the gate that fails was the one never scored.

## The structural hole

Reading `quant_engine/options/wheel_backtest.py`, the state machine is:

    flat → short_put → (assigned) → shares_pending → covered_call → (called away) → flat

**There is no exit from the shares except being called away.** Once assigned,
the position holds through any decline, indefinitely, while writing calls
against it. META fell 71% and the wheel held every point of it. That is not a
tuning fault — the strategy as specified has no way to stop losing.

## The change being tested — ONE thing

A stop on the ASSIGNED SHARES: if the close falls `assigned_stop_pct` below the
assignment cost basis, sell the shares, take the loss, return to flat and
resume selling puts. Nothing else changes — same entries, same premium floors,
same trend floor, same earnings veto.

Tested at 15%, 20%, 25%, 30% and 40% below cost basis, plus 0 (off) as control.

## Gates — unchanged from v3, all must pass

The gates are NOT relaxed for this attempt. If a stop cannot make the original
gates pass, the answer is that the wheel does not work, not that the bar was
too high.

| # | test |
|---|---|
| G3 | full-period net CAGR ≥ 8%/yr |
| G4 | worst single-symbol drawdown ≤ 40% |
| plus the remaining v3 gates as scored by the harness |

## Prediction, written before the run

**G4 will pass at any stop tighter than 40%** — that is close to arithmetic: a
stop at −30% of cost basis cannot produce a −71% single-name drawdown unless
the gap through it is enormous.

**G3 is where this dies, and I expect it to.** v3 already sat at 7.61% against
an 8% gate — 0.39 points short *before* adding a mechanism that converts
unrealised drawdowns into realised losses. A stop pays for lower drawdown with
lower return, and there is no headroom to pay from.

**So my honest prediction is: G4 passes, G3 fails, verdict unchanged.** I am
running it because that prediction is worth testing rather than asserting, and
because the SHAPE of the G3/G4 trade-off across stop levels tells us whether
the wheel is marginal or hopeless — which is a different and more useful
question than "does it pass".

**What would surprise me:** a stop that IMPROVES CAGR. That would mean the
assigned positions were not just drawing down but were a net drag the whole
time, and it would point at the universe rather than the mechanism.

**If nothing passes**, the recommendation stays DO NOT FUND, and the honest
next question is whether a wheel on a different universe (index ETFs only,
where a −71% single name is not possible) is a different strategy worth its own
gates — not whether this one can be tuned into shape.
