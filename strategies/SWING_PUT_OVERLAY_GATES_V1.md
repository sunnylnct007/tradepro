# SWING PUT OVERLAY GATES V1 — sell a weekly put on a swing signal instead of buying?

20 Sep 2026. Owner: *"we can have genuine swing candidate and genuine
opportunity to sell put without getting assigned"*.

The idea is sound on its face and worth testing rather than arguing about. The
swing rule already identifies a name that has fallen 2.25σ below its 20-day
mean while still above its 200-day average, and reverts within 20 sessions
about 70% of the time. Selling a put against that signal means being **paid to
buy a dip you had already decided you wanted**, and if the dip reverts the put
expires worthless and no stock is ever owned.

## What is measurable and what is modelled — stated up front
**Assignment is exact.** Whether a put finishes in the money is pure price
data: did the close on expiry sit below the strike. No option prices are
needed and none are assumed. That is the number the owner's question turns on
("without getting assigned") and it is the one this study can settle outright.

**Premium is modelled.** We hold ~25 days of option history, nowhere near
enough to price 27,000 historical weeklies. Premium comes from Black-Scholes
on trailing realised volatility, and every yield below is therefore an
ESTIMATE. Saturday's theta study showed what that omits: the median put
bid-ask is 8.9% of mid, and crossing it is the single largest cost in the
trade. **Any yield quoted here is a ceiling, not an expectation.**

## The test
For every swing signal in the traded universe, on the signal close:

- sell a put expiring in **7 calendar days**, at strikes of ATM, −3%, −5% and
  −8% (the last being the swing rule's own stop level)
- assignment = the close on expiry is below the strike
- compare against the baseline the desk already trades: BUYING the stock on
  the same signal, exits on target / −8% stop / 20 sessions

Run on the same 5,416 in-sample signals the sizing study used, so the two are
directly comparable.

## Gates — the overlay is worth building only if ALL hold
- **G0** n ≥ 1,000 signals with a usable 7-day forward window
- **G1** assignment rate at the −5% strike ≤ **20%** — the owner's condition
  is "without getting assigned", and a strategy assigned on one trade in four
  is a stock-buying programme wearing a costume
- **G2** the modelled annualised yield at that strike ≥ **8%** BEFORE spread
  costs — below that it cannot survive an 8.9% spread and is not worth the
  operational load
- **G3** when assignment DOES happen, the average outcome is no worse than
  simply having bought the stock on the same signal. Assignment is only
  acceptable if it lands you where you were going anyway
- **G4** the assignment rate holds in both halves of the period, so it is not
  a bull-market artefact

## Prediction (recorded before the run)
**G1 passes at −5%. G2 fails.**

Reasoning: a 7-day window is short, and a name 2.25σ below its mean that is
still above a rising 200-day average is more likely to bounce than to fall
another 5% inside a week. I expect assignment at −5% around 8–12%.

But that is exactly why the premium will be thin. A put 5% out with a week to
run is cheap in absolute terms, and the annualised figure is flattered by
dividing a small number by 7/365. Against a real 8.9% spread I expect the net
to be poor. My guess is a modelled 6–12% annualised gross, most of which the
spread eats.

I also expect the comparison to be unflattering in a way the owner will not
like but should see: **buying the stock on the same signal returns +0.81% per
trade over ~10 sessions.** The put overlay converts that into a capped, much
smaller gain in exchange for a lower hit rate. It trades upside for
consistency, and the dip-buy edge we have measured lives precisely in the
upside.

The outcome I would find most interesting is **G1 passing while G3 fails** —
rare assignment, but concentrated in exactly the cases where the dip kept
falling, which is when you least want the stock.

## Consequences, pre-stated
- **All pass** → build it as a real lane, pre-registered, sized separately.
- **G2 alone fails** → the structure is sound but not at our size; record it
  and revisit only if the spread problem is solved.
- **G1 or G3 fails** → the "without getting assigned" premise is wrong, and
  the honest answer to the owner is that this is a stock-buying strategy with
  extra steps.
- Nothing here changes the swing rule, which stays as it is either way.

## RESULT — run 20 Sep 2026. The premise is RIGHT. The trade still loses.

26,850 swing signals across the 956-name universe, 2006 → 2026.

| strike | n | assigned | gross ann. yield | stock move that week |
|---|---|---|---|---|
| ATM | 26,901 | 42.2% | 76.0% | +0.82% |
| −3% | 26,887 | 16.1% | 26.1% | +0.82% |
| **−5%** | **26,850** | **8.4%** | **12.6%** | +0.83% |
| −8% (the swing stop) | 26,775 | 3.4% | 4.6% | +0.83% |

**G0 PASS · G1 PASS (8.4%) · G2 PASS (12.6%) · G4 PASS (6.7% / 10.1%) ·
G3 FAIL.**

### The owner was right about assignment
*"without getting assigned"* — at the −5% strike you are assigned on **8.4%**
of signals, and at −8% on 3.4%. A name 2.25σ below its mean while above a
rising 200-day average really does bounce more often than it falls another 5%
inside a week. That half of the idea is confirmed on 26,850 cases.

### And it does not matter, because of WHICH 8.4%
G3 is the gate that fails, and it fails hard. In the weeks where assignment
happens, the stock is down **−8.46%** — against +0.83% across all signals. The
assignments are not a random sample of the trades; they are precisely the
dips that kept falling. Selling the put hands you the stock only when the
signal was wrong.

Priced out per weekly cycle on the collateral a cash-secured put ties up:

    premium collected                    +0.242%
    assigned 8.4% of the time, at −3.64% mark-to-market
    EV = 0.916 × (+0.242%) + 0.084 × (+0.242% − 3.64%)
       = −0.064% per week
       = −3.34% annualised, GROSS of spread

Buying the stock on the same signal returns **+0.89%/trade** over ~10 sessions
— roughly +4.5% annualised on deployed capital.

So the overlay converts a positive edge into a negative one. It collects a
thin, certain premium 92% of the time and pays for it with a fat, adverse loss
8% of the time. That is the classic short-put shape, and this desk has now
measured it twice: Saturday's theta study found the same thing from the other
end — short-put P&L is **73% profitable when the underlying rises and 8% when
it falls**, which is not an income trade at all.

### My prediction, scored
- G1 passes at 8–12% → **right** (8.4%)
- G2 fails → **wrong**, it passed at 12.6%. I underestimated the premium on a
  2.25σ dip, where realised vol is elevated exactly when we are selling.
- *"The outcome I would find most interesting is G1 passing while G3 fails —
  rare assignment, but concentrated in exactly the cases where the dip kept
  falling"* → **this is what happened.** Recorded before the run.

### Consequence
**DO NOT BUILD.** Per the pre-stated consequence for a G3 failure: the
"without getting assigned" premise is correct but insufficient, and the honest
answer is that this is a stock-buying strategy with extra steps — one that
gives away the upside where the measured edge actually lives.

The swing rule is unchanged. If the owner wants premium income from these
signals the question to ask is not "does it avoid assignment" but "does the
premium cover the adverse selection", and at a weekly tenor it does not.

One caveat kept in view: premium here is Black-Scholes on trailing realised
vol, not quoted prices. Real weeklies on a name that just fell 2.25σ may price
richer than the model says. That would raise the premium leg but not touch the
−3.64% assignment leg, and the gap to close is 0.064%/week against a 0.242%
premium — it would take roughly a **26% higher** premium than modelled to
break even, before spread.
