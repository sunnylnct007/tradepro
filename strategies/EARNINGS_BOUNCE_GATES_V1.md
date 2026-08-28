# Earnings-driven bounce — PRE-REGISTERED gates

**Committed BEFORE the run**, 28 Aug 2026.

## The owner's hypothesis

> "earnings calendar provides a good opportunity — fundamentally good stocks
> tend to bounce back"

Reading it precisely: a drop caused by an EARNINGS EVENT, in a name that is
otherwise sound, reverts more reliably than an ordinary drop. If true, earnings
proximity is a signal the current rule ignores entirely — it sees only price.

## Two questions, because the claim contains two

**Q1 — does earnings proximity IMPROVE the existing rule?**
Split every trade the live rule takes into EARNINGS-DRIVEN (a reported event in
the 3 sessions up to and including the signal bar) vs the rest, and compare.

**Q2 — does earnings proximity find trades the rule MISSES?**
The rule needs 2.5σ. Most earnings drops are smaller. Test a looser entry that
fires ONLY on an earnings drop: close down >= 5% on an earnings session, still
above the 200-SMA. Same target (20-day mean), same -8% stop, same 20-bar cap.

"Fundamentally good" has no feed in this repo. The 200-SMA filter is the only
quality proxy available and it is a crude one. **This study therefore cannot
test the owner's claim as stated** — it tests "in an uptrend", not "fundamentally
sound". Stated here so no result can quietly claim more.

## Gates — a candidate must clear ALL

| # | Test | Threshold |
|---|------|-----------|
| **V0** | Trades | >= 300 |
| **E1** | Win rate | >= 55% |
| **E2** | Mean return per trade | > 0 |
| **E3** | Beats the comparison arm on mean/trade | by >= 0.20pt |
| **E4** | Survives the TWO-SPLIT | positive in ALL FOUR cells (time x symbol) |
| **E5** | Worst single trade | >= -25% |

E4 is the gate that has rejected momentum v3, intraday dip, both resting-limit
studies, four ranking rules and early entry. It is the one that matters.

## COVERAGE LIMIT, carried with every result

Earnings history holds 5,062 events across 205 of 244 symbols, but only **7
events are pre-2020** and only **1 symbol of 205** has any pre-2020 event.
Median first event is 2020-10-29.

So the TIME split is 2020-2023 vs 2023-2026 — two halves of ONE regime, not two
regimes. This repo has already been burned by an effect that inverted pre-2020
(the volume-ratio rule). **A pass here is weaker evidence than a pass on the
price-only studies**, and must not be reported as equivalent.

## Prediction — recorded before the work, and it contradicts the hypothesis

**I expect Q1 to FAIL and earnings-driven drops to bounce WORSE**, not better.
An earnings drop is INFORMATION — the market re-rating the business on new
facts. An ordinary 2.5σ drop with no news is more likely noise, and noise is
what mean reversion feeds on. Buying the dip is a bet that nothing changed;
an earnings miss is the case where something did.

**I expect Q2 to fail V0/E1 on quality**: a 5% earnings drop is a low bar and
will admit many names in genuine decline.

If I am wrong on Q1 — if earnings drops bounce BETTER — that is a real finding
and directly contradicts my reasoning above. It would mean the market
systematically overreacts to earnings in uptrending names, which is a
documented effect and would be the first thing this session found that ADDS to
the rule rather than failing to.

**I will not move these gates after seeing the numbers.** If E3 comes in at
0.19pt it fails.
