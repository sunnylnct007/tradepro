# QUIVER CONGRESS GATES V1 — does following disclosed congressional buying work?

15 Sep 2026. I dismissed congressional trading on **a priori** grounds: the
median disclosure lag is 33 days (p90 116) against a swing rule that holds 20
sessions ≈ 28 calendar days, so the typical trade is visible only after our
position would have closed.

That argument is reasonable and it is still only an argument. The historical
endpoint returns **ten years** (`/beta/historical/congresstrading/{ticker}`,
2016 → 2026, 650 rows for NVDA alone), so the question is testable and it
costs nothing to settle properly. Asserting where I can measure is exactly
the habit this desk has spent a fortnight removing.

## The test
For every disclosed PURCHASE by a member of Congress in a symbol our store
covers, enter at the close of the **disclosure date** (`ReportDate` — the
first moment the information is public; using `TransactionDate` would be
lookahead we could never have traded) and hold for a fixed horizon. Compare
against a **placebo**: the same count of entries on random dates in the same
symbols over the same period, which controls for the fact that these are
mostly large-cap names in a rising market.

Horizons: 21, 63 and 126 sessions — deliberately including horizons LONGER
than our lanes, so a real effect that is simply too slow for us is
distinguishable from no effect at all. Those are different findings.

## Gates — congressional buying is judged INFORMATIVE only if ALL hold
G0 n ≥ 500 disclosed purchases with usable price history
G1 mean return beats the placebo mean by ≥ 0.50pp at the SAME horizon
G2 the edge holds in BOTH halves of the period (pre-2021 and post-2021) —
   a signal that only worked before it became famous is not a signal
G3 it survives the SPY-relative version: excess return over SPY beats the
   placebo's excess by ≥ 0.50pp, so a rising market is not the explanation
G4 median (not just mean) beats the placebo median, so a handful of tail
   winners cannot carry it

## Consequences, pre-stated
- **All five pass at 21 sessions** → the lag argument was wrong and this
  belongs in the desk as a real input. Pre-register a lane before building.
- **Passes only at 63/126** → real but too slow for our lanes. It becomes a
  documented finding and a reason to consider a longer-horizon sleeve; it
  does NOT get bolted onto swing.
- **Fails** → congressional trading is retired to display-only, and the
  a priori lag argument is retired too, because it will have been confirmed
  by measurement rather than assumed.

## Prediction (recorded before the run)
Fails G1 at every horizon. The dataset is the most publicised alternative
feed in existence, the disclosure is late by construction, and the trades are
overwhelmingly large-cap names that a placebo in the same names over the same
decade will match. I give it ~15%. If it passes G1 but fails G2, that is the
most likely interesting outcome: an edge that existed before 2021 and was
arbitraged away once the data became a retail product.

## RESULT — run 15 Sep 2026. FAILS at every horizon.

102 of our 244 symbols have congressional history; 7,683 disclosed purchases
with usable price data.

| horizon | n | congress | placebo | edge | cong median | placebo median |
|---|---|---|---|---|---|---|
| 21s | 7682 | +1.94% | +2.73% | **−0.79** | +1.58% | +1.51% |
| 63s | 7682 | +6.06% | +7.89% | **−1.83** | +4.42% | +4.06% |
| 126s | 7682 | +10.89% | +17.67% | **−6.79** | +8.21% | +8.13% |

SPY-relative excess is negative at all three horizons too (−1.58, −1.58,
−2.48). **G1 and G3 fail everywhere.** G0 and G4 pass — the median is
fractionally better than the placebo's — but a signal whose mean loses to
random dates by 0.8pp at our horizon and 6.8pp at six months is not a signal.

## Where my prediction was right, and where it was wrong

Right on the verdict (I said ~15% and it fails). **Wrong on the mechanism.**
I expected an edge that existed and was then arbitraged away once the data
became a retail product. The era split says the reverse:

| horizon | pre-2021 (vs placebo) | post-2021 (vs placebo) |
|---|---|---|
| 21s | +1.75% vs +3.69% | +2.11% vs +1.45% |
| 63s | +6.31% vs +10.69% | +5.84% vs +4.16% |
| 126s | +11.28% vs +24.31% | +10.54% vs +8.88% |

Congress looks WORSE than the placebo before 2021 and slightly BETTER after.
I do not believe that is a real reversal — it is the placebo moving, not the
signal: random dates drawn from 2019-2020 catch the post-COVID recovery, which
inflates the pre-2021 placebo enormously (+24.31% at 126 sessions). The era
split is confounded by regime and should not be read as evidence either way.
Recorded because it was pre-registered, not because it is interpretable.

The honest correction to my own reasoning: the lag argument was not what
killed this. **Congressional purchases simply do not beat a random date in
the same names.** They are mostly large caps bought during a bull decade, and
the placebo captures exactly that. The disclosure delay is a second reason,
not the reason.

## Consequence
Congressional trading is retired — not displayed, not scouted, not gated. It
stays reachable in the client for anyone who asks, labelled with this result.
Insider buying and federal contracts remain as display context, still
untested on our names (insiders cannot be tested until the daily capture
accumulates; that is why it now runs).
