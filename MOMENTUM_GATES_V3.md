# Momentum v3 — does entry-bar VOLUME add anything?

**Status: PRE-REGISTERED. Committed BEFORE the run.**
Supersedes nothing. v2 (`MOMENTUM_GATES_V2.md`, ca494bf) stays live on the
Momentum screen unless v3 passes every gate below.

## Why this study exists

Answering "is PLTR a fake-out" I bucketed every historical v2 entry by the
entry bar's volume against its own 20-day average, and found a monotonic
spread:

| entry-bar volume | trades | win% | mean% | median% |
|---|---|---|---|---|
| < 0.7x  | 1,508 | 45.4% | +2.23% | **-1.24%** |
| 0.7-1.2x | 2,704 | 49.1% | +1.90% | -0.21% |
| > 1.2x  | 1,184 | **52.2%** | +2.84% | **+0.57%** |

That is a real-looking effect. It is also **exactly the kind of finding that
should not be trusted**: it was discovered by slicing a sample I had already
seen the answer on. Bolting it onto v2 because the table looks good is
tuning, and this repo's whole protocol exists to stop that.

So it gets its own pre-registered run, and the burden of proof sits on the
volume filter, not on v2.

## The honest limitation, stated up front

There is no untouched hold-out. The effect was found on the full history, so
**no test here can be a true out-of-sample test.** The strongest available
substitute is a demand that the effect survive being cut two independent
ways — by TIME and by SYMBOL — with the direction holding in all four cells.
A filter that only works in one half is curve-fit and must be rejected.

This is a weaker claim than OOS and the result must be reported as such.

## Variants

All identical to v2 except for an added condition on the entry bar:

* **A — control**: v2 exactly. No volume condition.
* **B — >= 0.7x**: excludes only the dried-up bucket.
* **C — >= 1.0x**: at least average volume.
* **D — >= 1.2x**: the elevated bucket that scored best.

Volume ratio = entry-bar volume / mean volume of the prior 20 bars. Symbols
with no usable volume history are excluded from ALL variants including the
control, so the four are compared on identical trade populations.

Everything else is unchanged: pullback to the 10-SMA in an uptrend, hard -8%
stop, 8% trailing stop, 60-session timeout, and the >35% single-session
corrupt-bar discard.

## Gates

A variant SHIPS only if it passes **every** gate.

| # | Gate | Threshold | Why this number |
|---|---|---|---|
| G1 | Completed trades | >= 1,500 | v2 has 5,815. Below ~1,500 the per-symbol record in the drill-down becomes too thin to show. |
| G2 | Win rate | >= 50.0% | v2 = 47.0%. The claimed improvement has to actually appear. |
| G3 | Mean per trade | >= +2.00% | v2 = +1.53%. A ~30% lift, or the added complexity is not worth carrying. |
| G4 | **Median per trade** | > 0.00% | The headline gate. v2's median trade LOSES money; the mean is carried by a tail. If volume selection cannot make the typical trade profitable it has not fixed the thing worth fixing. |
| G5 | Worst single trade | >= -25.0% | No fatter tail than v2's buckets showed. |
| G6 | Median hold | <= 40 sessions | Same declared scope as v2. |
| G7 | **Survives both splits** | Direction holds in all 4 cells | Time split (first/second half) AND symbol split (alternating). Mean AND win rate must beat the control in every cell. This is the anti-curve-fit gate and the one most likely to fail. |
| G8 | Signal frequency | >= 0.5 candidates/session | A screen that fires twice a month is not a sleeve you can run. |

## Prediction, on record, before running

Written now so it can be graded against the result rather than rationalised
after it:

1. **The effect will hold directionally but shrink.** Full-sample bucketing
   flatters; I expect roughly half the apparent lift to survive.
2. **D (>=1.2x) FAILS G1.** ~1,184 trades in the bucket, below the 1,500
   floor. It will probably post the best per-trade numbers and be
   unshippable anyway.
3. **C (>=1.0x) is the likeliest to ship**, and I expect it to pass G2 and
   G3 but be **marginal on G4** — I predict a median between -0.1% and
   +0.4%, i.e. genuinely near the line.
4. **G7 is where this most likely dies.** I put the chance that a filter
   passes all of G1-G6 and then survives all four split cells at **under
   50%**.
5. **B (>=0.7x) passes G1 comfortably and fails G3** — excluding only the
   worst bucket is too blunt to move the mean 30%.

If nothing passes, v2 stays exactly as it is and this file is kept as a
failed study, like the other four.
