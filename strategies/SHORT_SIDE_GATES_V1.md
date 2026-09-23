# SHORT SIDE GATES V1 — if we can call the upside, can we call the downside?

23 Sep 2026. Owner: *"if we can tell the upward we shd be able to tell the
downward"*.

It is the obvious question and it deserves a measured answer rather than an
opinion. Two rules on this desk have passed pre-registered gates on the long
side, on committed harnesses:

    Swing (mean reversion)  +0.90%/trade, 71% win, 21,948 out-of-sample trades
    Momentum (pullback)     +1.53%/trade, 47% win, 5,815 trades, worst -14.7%

This tests their exact mirrors. Nothing is re-tuned; the parameters are the
long rules' own, inverted.

## The two mirrors

**SHORT MEAN REVERSION** — fade a spike instead of buying a dip.
Enter when the close is **+2.25σ ABOVE** its 20-day mean while **BELOW** the
200-day average (the long rule requires below-band and above-200). Exit on a
return to the mean, a **+8%** stop, or 20 sessions.

**SHORT MOMENTUM** — ride a downtrend instead of an uptrend.
Enter on a rally back to the 10-SMA inside an established DOWNtrend: 20-SMA <
50-SMA, close < 20-SMA, close back at the 10-SMA, close < 200-SMA. Exit on an
8% trailing stop from the trough, a hard **+8%** stop, or 60 sessions.

Same 956-name universe, same 2006-2026 window, same harness, same fill
assumptions as the long studies, so the comparison is like-for-like.

## Why symmetry is NOT the null hypothesis
The intuition — an edge that reads direction should read it both ways — assumes
a symmetric market. Equities are not:

- **Drift.** US equities rose over the test window. A short fights that
  constantly; a long is carried by it. This is not a small term over twenty
  years.
- **Skew.** Downside moves are faster and deeper than upside ones, which cuts
  both ways: a short entered well pays quickly, and a short entered badly is
  run over faster than a long.
- **The tail is on the wrong side.** A long's worst case is −100%. A short's is
  unbounded, and the −8% stop already failed to cap the long side at −8%
  (measured worst: −32.6% on gaps). On a short that same gap mechanic is
  uncapped.

So the honest null is that these FAIL, and the interesting question is whether
they fail on the mean, the tail, or only in the bull regime.

## Gates — the mirror works only if all hold
Identical thresholds to the long studies, so neither side gets an easier test.

- **V0** n ≥ 1,000 trades
- **G1** win rate ≥ 45%
- **G2** mean return per trade > 0, net
- **G3** median hold ≤ 40 bars
- **G4** top-1% of trades contribute ≤ 35% of total profit
- **G5** worst single trade ≥ −25%
- **G6** (short-only, added deliberately) the mean is positive in BOTH halves of
  the period. A short strategy that only works in 2008 and 2020 is a crash
  hedge, not a signal, and should be described as one.

## Prediction (recorded before the run)
**Both fail G2 — the mean per trade is negative.** I give "either mirror passes
all gates" about 15%.

Reasoning: over 2006-2026 the index roughly tripled. A rule that is short by
construction pays that drift on every trade it holds, and neither mirror has
any mechanism to avoid it — the 200-day filter selects names already falling,
which is exactly where a violent bear-market rally does the most damage to a
short.

**Short momentum is the likelier of the two to survive**, because a trailing
stop on a genuine downtrend can ride 2008 and 2020, and those two windows are
large enough in a twenty-year sample to carry a mean. If it passes G2 and fails
G6, that is the most informative outcome available here and I expect it:
**a crash hedge wearing a signal's clothes.**

**Short mean reversion I expect to fail worst.** Fading a spike in a downtrend
is selling into the one condition — a short squeeze — that produces the fastest
adverse move in equities.

## Consequences, pre-stated
- **All gates pass** → a short lane is justified. It would still need its own
  sizing study before trading, because borrow, hard-to-borrow fees and
  unbounded loss are not modelled here.
- **Passes G1-G5, fails G6** → it is a crash hedge. Record it as such, do not
  run it as a signal, and revisit only if the book ever needs a hedge.
- **Fails G2** → the answer to the owner's question is no, and the reason is
  drift rather than any defect in the rules. Say so plainly and stop.
- Either way the long rules are unchanged; this measures a mirror, not them.

## RESULT — run 23 Sep 2026. The answer is asymmetric, and not where I expected.

956 symbols, 2006-11 → 2026-09, same harness family as the long studies.

| mirror | n | win | mean/trade | hold | top1% | worst | early/late |
|---|---|---|---|---|---|---|---|
| **short mean reversion** | 12,827 | **57.1%** | **+0.25%** | 7b | 9% | −30.7% | +0.43 / +0.08 |
| short momentum | 32,288 | 32.1% | **−1.26%** | 8b | 15% | −27.2% | −1.15 / −1.37 |

    short mean reversion   V0 G1 G2 G3 G4 G6 PASS · G5 FAIL (-30.7% vs -25%)
    short momentum         G1 G2 G5 G6 FAIL

**Neither passes. Both frozen verdicts stand.** But the shape of the failure is
the finding.

## My prediction was wrong, and backwards
Recorded before the run: *"Both fail G2... Short momentum is the likelier of the
two to survive... Short mean reversion I expect to fail worst."*

Short mean reversion **passed G2** at +0.25%/trade over 12,827 trades and cleared
six of seven gates. Short momentum failed at **−1.26%/trade** across 32,288
trades and was the worse of the two by a wide margin.

I also blamed drift in advance, and drift is not the explanation. Both mirrors
hold 7-8 bars — near-identical exposure to it. What separates them is the HIT
RATE: 57.1% against 32.1%.

## What this actually says
**Mean reversion is the symmetric edge. Momentum is not.**

    long  mean reversion   71.0% win   +0.90%/trade
    short mean reversion   57.1% win   +0.25%/trade
    long  momentum         47.0% win   +1.53%/trade
    short momentum         32.1% win   -1.26%/trade

A stretched price reverts in both directions — less reliably downward, but
reliably enough to show up across 12,827 trades and in both halves of twenty
years. A trend, by contrast, only pays upward on this universe: shorting one is
selling into the violent bounces that define bear markets, and a 32% hit rate
is what that looks like.

So the owner's intuition — *if we can tell the upward we should be able to tell
the downward* — holds for the mean-reversion rule and does not hold for
momentum. It was right about the mechanism and wrong about which mechanism.

## Consequence — do NOT trade the short mirror
Per the pre-stated consequences, neither mirror licenses a lane. Short mean
reversion deserves the specific reason rather than a shrug:

1. **The edge is thin and decaying.** +0.25%/trade is a quarter of the long
   rule's, and the halves read +0.43% then +0.08%. Whatever is there has been
   arbitraged down.
2. **The tail is the wrong way round.** G5 failed at −30.7% against an +8% stop
   — gaps go straight through, exactly as on the long side. But a long's worst
   case is bounded at −100% and a short's is not. The same failure is a
   materially worse risk here.
3. **Nothing models borrow.** Hard-to-borrow fees and recalls are unpriced in
   this harness and land hardest on precisely the names a spike-fade selects.

+0.25% per trade does not buy an uncapped tail. The honest answer to the
question is **no** — not because the downside is unreadable, but because what
is readable there is not worth what it costs.

The long rules are unchanged.
