# Mean reversion SHORT — tested, does not work. 23 Aug 2026.

Owner: *"won't the same strategy apply to short sell"*. Reasonable question,
and the answer is no. Recorded so it is not re-derived.

## The mirror, exactly inverted

    LONG   close < 2.5σ BELOW the 20-day mean, while ABOVE the 200-SMA
    SHORT  close > 2.5σ ABOVE the 20-day mean, while BELOW the 200-SMA

Target the 20-day mean either way; stop 8% against; 20-session cap; entry at
the next open.

| variant | trades | win% | mean% | median% | worst% |
|---|---|---|---|---|---|
| **LONG dip, above 200-SMA (live)** | 2,453 | **72.6%** | **+0.95%** | +1.79% | −24.0% |
| SHORT spike, below 200-SMA | 905 | 52.6% | **−0.64%** | +0.68% | −31.1% |
| SHORT spike, no trend filter | 5,957 | 54.2% | **−0.65%** | +0.63% | **−51.7%** |
| LONG dip, no trend filter | 5,077 | 68.0% | +1.07% | +1.90% | −30.7% |

## Why, and it is not bad luck

**The asymmetry was already measured elsewhere in this project.** Stocks drift
UP — about +0.08% a day across 530,286 stock-days — and **69% of that drift
accrues OVERNIGHT**. A long holder earns it every night held; a short holder
pays it. Part of the long side's edge is simply riding that drift, and
reversing the rule turns a tailwind into a headwind.

The tail is worse for a structural reason too: **−51.7% worst trade against
the long side's −24%**. A long position can only fall to zero. A short can be
squeezed without bound, and the 8% stop is checked on the CLOSE, so a gap runs
straight through it.

Note the median short trade is POSITIVE (+0.68%) while the mean is negative.
The typical short works and the average loses — the losses are large and rare,
which is the worst distribution to hold with a fixed stop.

## Incidental finding worth keeping

Dropping the 200-SMA filter from the LONG rule RAISES the mean (+1.07% vs
+0.95%) on twice the trades — but the worst trade degrades from −24.0% to
−30.7%. The trend floor is buying tail protection with a little expectancy.
That is a defensible trade and it is what shipped, but it is a CHOICE, not a
free improvement, and it should be re-examined deliberately rather than
inherited.

## Also, a factual correction

Shorting does not require squaring off the same day. A short can be carried
overnight subject to borrow availability and margin; same-day closure is a
feature of specific intraday products in some markets, not of short selling.
The reason not to short here is the evidence above, not a settlement rule.
