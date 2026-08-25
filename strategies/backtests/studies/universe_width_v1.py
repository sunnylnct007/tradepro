"""Should we evaluate MORE symbols? Measured, 25 Aug 2026.

Owner: "we should try to evaluate more symbols."

Reasonable instinct, and with a concurrency cap it has a clear mechanism
behind it: if you can only hold 12 positions, picking those 12 from a larger
pool should give you better ones. That is the argument. It does not survive
measurement, and the reason it fails is more useful than the result.

## The pool

The committed universe is 244 names. The store holds 991. Of the 747
outside it, **732 pass every published criterion** — price, session count,
phantom check, and dollar volume. They are not excluded by a rule; they were
simply never added. The universe file is a snapshot, not a filter applied
continuously.

(One caveat that limits any expansion right now: the dollar-volume test is the
liquidity gate, and it runs on the stored volume series, which the data lane
has shown is x100 inflated for some months and sources. With volume inflated
100x, a $10M floor admits almost everything. **The universe's main defence is
currently not functioning**, so "732 pass" should be read as "732 are not
currently excluded", which is a weaker statement.)

## The result — wider is WORSE

Capped at 12, ranked by reward:risk:

    pool          names   signals   taken   mean     worst    win
    committed       244     2,503   1,658  +0.76%   -17.7%   69.7%
    everything      991     3,627   1,952  +0.66%   -21.6%   68.6%

Widening the pool costs 0.10 points per trade, 3.9 points on the worst trade,
and 1.1 points of win rate.

## Why, and this is the part worth keeping

The extra names are NOT junk:

    signal quality, ungated    244 universe names  +1.10%
                               747 extra names     +0.93%

Only 0.17 points apart. But their **reward:risk scores are HIGHER** — 0.83
against 0.75 — so the ranking rule systematically prefers them. They take 27%
of all slots, and deliver +0.63% against the universe names' +0.67%.

**The ranking rule is volatility-biased, and it only shows up when the pool is
heterogeneous.** Reward:risk is upside-to-target over a fixed 8% stop. A more
volatile name sits further from its 20-day mean when it dips, so it scores
higher — without being a better trade.

This is the same criticism I made of deepest-sigma, inverted. Sigma normalises
by the symbol's own volatility, which is wrong when the stop is absolute.
Reward:risk does not normalise at all, which is fine on a pool of similar
names and wrong on a mixed one. **On the 244-name universe reward:risk is
correct and passes all four two-split cells. On a 991-name pool it drifts
toward the volatile tail.**

So the universe definition and the ranking rule are doing a job TOGETHER: the
universe makes the pool homogeneous enough that an un-normalised ranking is
safe. Widen one without fixing the other and the pair breaks.

## What would make expansion work

Not "add more names". Either:

  1. a ranking rule that is not volatility-biased — and it would need its own
     pre-registered gates and its own two-split, because the obvious
     normalisations are exactly what already failed; or
  2. a working liquidity gate, so the pool stays homogeneous as it grows —
     which is blocked on the volume repair, item 7 of the post-window store
     session.

Both are real work. Neither is "evaluate more symbols".

## Prediction vs result

I predicted a modest IMPROVEMENT in mean and a worse worst trade. The worst
trade did get worse, -17.7% to -21.6%, as expected. The mean FELL, which I did
not expect, and the diagnosis above is why.
