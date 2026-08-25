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

---

# RESULT — DO NOT FUND stands. And a stop can make drawdown WORSE.

Full window, v3 configuration, stop swept:

| stop | G3 · full-period CAGR (≥8%) | G4 · worst single name (≤40%) |
|---|---|---|
| **off (v3 control)** | 7.74% FAIL | META −71.4% FAIL |
| 15% | 5.30% FAIL | TSLA −51.5% FAIL |
| **20%** | 5.67% FAIL | **META −29.4% PASS** |
| **25%** | 6.10% FAIL | **META −29.4% PASS** |
| 30% | 6.05% FAIL | TSLA −57.8% FAIL |
| 40% | 6.13% FAIL | TSLA −57.6% FAIL |

**G3 fails at every level, and the control is the BEST of them.** Every stop
costs 1.6 to 2.4 points of CAGR. There was never headroom to pay from — v3 sat
0.26 points short before the stop was added.

## My prediction was half right, and the wrong half is the interesting one

I predicted "G4 will pass at any stop tighter than 40% — that is close to
arithmetic". **It is not, and the failure is instructive.** A 15% stop makes
G4 WORSE than a 20% stop (−51.5% vs −29.4%), and 30% and 40% fail too.

The reason: **stopping out returns the sleeve to FLAT, and flat sells puts
again.** In a sustained decline a tight stop cycles you through assignment →
stop → re-assignment → stop, and the cumulative loss of several round trips
exceeds a single hold. The stop does not cap the drawdown; it re-enters into
the same decline. That is why 15% is worse than 20%, and why the relationship
is not monotonic at all.

I asserted arithmetic where the mechanism was a feedback loop. The stop is not
a floor under one position — it is a permission to take the position again.

## The honest shape of this strategy

The wheel trades return for drawdown, and **no point on that curve clears both
gates**:

* the best return available is 7.74% CAGR, and it comes with a −71.4%
  single-name drawdown
* the best drawdown available is −29.4%, and it comes with 5.67–6.10% CAGR

So it is **marginal on each gate separately and hopeless on both together**.
That answers the question the sweep was run for — marginal or hopeless — more
usefully than pass/fail would have.

## Recommendation, unchanged

**DO NOT FUND.** Not because the bar is too high, but because the return is
~6–8% against an 8% requirement while carrying single-name drawdowns of 30–70%,
and the mechanism that produces the drawdown cannot be fixed from either end —
entry filters cannot reach it (v3) and exit stops make the return worse (v4).

**The one thing NOT tested here, and the honest next question if the owner
wants it pursued:** a wheel on index ETFs only, where a −71% single name is
structurally impossible. That is a different strategy on a different universe
and deserves its own gates file rather than a v5 of this one. It would also
earn far less premium, which is exactly the trade this whole exercise keeps
running into.
