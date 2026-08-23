# Shorting — PARKED workstream. Do not re-derive the dead end.

Owner, 23 Aug: *"park the strategy for shorting as well so we do not forget"*.

## What is already settled — do not repeat this

**The mean-reversion rule inverted DOES NOT WORK.** Tested and recorded in
`SHORT_SIDE_RESULT.md`:

| variant | trades | win% | mean% | worst% |
|---|---|---|---|---|
| LONG dip, above 200-SMA (live) | 2,453 | 72.6% | **+0.95%** | −24.0% |
| SHORT spike, below 200-SMA | 905 | 52.6% | **−0.64%** | −31.1% |
| SHORT spike, no trend filter | 5,957 | 54.2% | **−0.65%** | −51.7% |

Anyone tempted to "just flip the signs" should stop here.

## WHY it failed, which constrains what could work

1. **The drift is against you.** Stocks rise ~+0.08%/day across 530,286
   stock-days, and **69% of that accrues OVERNIGHT**. A long earns it every
   night held; a short pays it. Any short strategy must clear that hurdle
   before it clears anything else.
2. **The tail is unbounded.** Worst short trade −51.7% against the long
   side's −24.0%. A long can only reach zero; a short can be squeezed without
   limit, and a percentage stop checked on the CLOSE does not survive a gap.
3. **The distribution is the wrong shape to hold.** The MEDIAN short trade is
   POSITIVE (+0.68%) while the mean is negative — the typical short works and
   the average loses. Rare, large losses against a fixed stop is the hardest
   combination to trade psychologically and the easiest to be wrecked by.

## What a short strategy would have to look like instead

Not a mirror. The three failure causes above imply the shape:

* **Short the drift, not against it** — pairs or relative-value, where the
  long leg pays the overnight drift the short leg costs. Market-neutral by
  construction rather than by hope.
* **Bounded loss** — put spreads or defined-risk structures rather than naked
  short stock, which caps the squeeze rather than praying past it. This makes
  it an OPTIONS workstream, and the options desk already exists.
* **A catalyst, not a level.** Mean reversion says "it went too far". For a
  short that is exactly the thesis a squeeze punishes. A short needs a REASON
  the price should fall — earnings, guidance, a broken balance sheet — which
  is a fundamentals/catalyst problem, and the catalyst layer is the platform's
  longest-standing unbuilt gap.

## Prerequisites before this is worth starting

1. The Swing forward test finishes — one strategy proven end to end first.
2. Borrow availability and cost modelled. None of the backtests above include
   borrow fees, which on hard-to-borrow names can exceed the entire edge. A
   short backtest without borrow cost is not a backtest.
3. The catalyst layer exists, if the catalyst route is taken.

**Status: PARKED. Not started, deliberately.** The reason is not that shorting
is wrong — the owner approved intraday shorting in August — it is that the
obvious version is measured and dead, and the versions that might work each
depend on something not yet built.
