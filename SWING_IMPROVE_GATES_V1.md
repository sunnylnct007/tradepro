# Two ways to make Swing better. Pre-registered, 27 Aug 2026.

Owner: *"we need to first make the strategy better."* Two candidates, both
already half-measured, tested together because they are independent and each is
cheap.

## Candidate 1 — LOOSEN THE TRIGGER to 2.25σ

Measured 26 Aug across the whole sigma curve:

| σ | trades | win% | mean/trade | worst |
|---|---|---|---|---|
| 2.00 | 5,499 | 72.2% | +0.91% | **−27.9%** — fails G5 |
| **2.25** | **3,918** | **72.4%** | **+1.00%** | −23.2% |
| 2.50 (live) | 2,504 | 73.2% | +1.11% | −23.2% |
| 3.00 | 783 | 73.3% | +1.24% | −13.8% |

2.25σ gives **56% more trades** and passes every gate on the full sample. The
win rate is flat across the entire range (72–73%), so the rule is not a knife
edge. 2.00σ is already excluded — it breaches the −25% worst-trade gate.

**The problem with this candidate is not the numbers, it is the motive.** It
was measured the day the screen produced no signal, and "loosen it so something
fires" is the exact curve-fit these gates exist to prevent. It only earns a
change if it survives the two-split.

## Candidate 2 — SKIP SIGNALS WHEN THE MARKET IS BELOW ITS 200-DAY

Already measured and stated on the desk: the rule earns **+0.93%/trade while
the S&P is above its 200-day average and +0.24% below it** — a fourfold
difference. 2022, the only losing year in the whole record, is the bear market.

The filter is: no new entry when SPY closes below its own 200-day average on
the signal bar. It is one line, it uses data already held, and unlike an entry
filter on the symbol it addresses a REGIME rather than a setup — which matters
because every symbol-level filter tried on this rule has been rejected.

**The cost is real and must not be hidden:** those +0.24% trades are still
POSITIVE. Skipping them gives up profit for lower drawdown. The question is
whether the trade is worth it, not whether the filter "works".

## Gates — both candidates, all five

| # | test |
|---|---|
| **S1** | beats the live rule on mean return per trade |
| **S2** | survives the TIME split — both halves |
| **S3** | survives the SYMBOL split — both cells |
| **S4** | worst trade no more than 2 points worse than the live rule's |
| **S5** | total return over the full period is not lower — a change that improves the average by taking fewer trades has not improved anything |

S5 exists because candidate 1 and candidate 2 fail in opposite directions:
loosening takes MORE trades at a lower average, filtering takes FEWER at a
higher one. Mean-per-trade alone would reward the wrong one of those.

## Predictions, written before the run

**Candidate 1 (2.25σ): I expect it to FAIL S1.** Mean per trade falls +1.11% →
+1.00% by construction — that is already measured. It can only pass on S5
(total return), where 3,918 × 1.00% beats 2,504 × 1.11%. So the honest question
is whether "more trades at lower quality" is an improvement, and I expect the
two-split to be the decider.

**Candidate 2 (regime filter): I expect it to PASS S1 and S4 comfortably and
to be at risk on S5.** Removing the below-200 trades removes positive
expectancy, so total return may fall even as the average and the worst trade
improve.

**What would surprise me:** the regime filter improving total return as well.
That would mean the below-200 trades are not merely weaker but a net drag once
their drawdowns are counted, and it would make the case for shipping it
immediately rather than as a risk-management option.

**If both fail**, the answer is that 2.5σ with no regime filter is the best
version of this rule we can find, and the way to make the strategy better is
not to keep adjusting it — it is to get live evidence, which is what the
forward test is for.
