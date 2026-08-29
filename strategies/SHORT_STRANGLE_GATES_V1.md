# Intraday short strangle, low-volatility filter — PRE-REGISTERED gates

**Committed BEFORE the graded run (29 Aug 2026).** Exploratory numbers exist and
are quoted as the PRIOR, not the result.

## What the owner actually does

From three Zerodha screenshots and his description:

> *"the option strategy that needs to be placed only when volatility is less. so
> we assess before entering and we ensure its closed at the end of day so we not
> carrying any risk ... Think its like a strangle and on occasional days where
> market volatility increases we start converting to straddle to narrow loss."*

Read off the screenshots: short BANKNIFTY 57400 PE + 58400 CE, −150 each, MIS
(intraday), strikes ~±0.87% around spot. A later shot shows the same book with
strikes pulled to 600 apart after the put leg lost ₹16,849 — the conversion.

Trading it in India needs a broker we are not connected to, so:

> *"i would rather try it on IBKR platform to US index"* … *"i might want to do
> on indian market but provided no connectivity u can provide me a candidate …
> which i can manually place on a daily basis"*

So the deliverable is a CANDIDATE, emailed daily. Execution is the owner's.

## The mechanism, stated so it can be wrong

Selling a strangle harvests the **variance risk premium** — implied volatility
exceeds subsequently-realised volatility most of the time, so short premium has
a positive expectancy and a fat left tail.

The low-volatility filter does NOT increase that premium. Measured on 2,513 SPY
sessions, the mean per trade is **identical** with and without it (+20.5 vs
+20.2 per contract). What it changes is the chance of a large realised move:

    VIX bucket   n     VIX    median move   p90     p99
    Q1 low       630   12.3   0.21%         0.58%   1.20%
    Q4 high      628   25.9   0.79%         2.22%   4.11%

So the claim under test is **not "this filter makes more money"**. It is:

> The filter converts a strategy with a ruinous tail into one with a survivable
> tail, at the cost of three-quarters of the opportunities.

That is falsifiable, and it is the only claim the evidence supports.

## What is being tested

| | |
|---|---|
| underlying | SPY (US, tradeable on IBKR); BANKNIFTY reported separately for the manual candidate |
| entry | at the session open, when VIX closed in its bottom quartile the prior day |
| structure | short strangle, strikes ±0.5% of the open (SPY moves less than BANKNIFTY; ±0.87% there) |
| expiry | 1 DTE |
| exit | at the session close — no overnight position, ever |
| premium | Black-Scholes, IV taken from VIX |
| costs | commission + half-spread per leg, charged both ways |

**The VIX quartile is computed on a TRAILING basis only** — the boundary must
come from data available before the session, never from the whole sample. An
in-sample quartile would leak the future into the filter and is the single
easiest way to fake this result.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Low-VIX sessions | ≥ 500 |
| **G1** | Win rate | ≥ 85% |
| **G2** | Mean per trade, NET of costs | > 0 |
| **G3** | **Worst single day ≥ −25× the mean trade** — the tail claim, and the whole point | true |
| **G4** | p5 ≥ −5× the mean trade | true |
| **G5** | **Both halves** (2016-2021 / 2021-2026) pass G1, G2 and G3 | true |
| **G6** | **Survives an IV error**: re-price with IV at 0.85× and 1.15× VIX — G2 still holds | true |
| **G7** | **Beats the unfiltered version on tail**: worst day at least 3× better than trading every day | true |

**G3 and G7 carry this study.** G1 and G2 would pass for almost any short-premium
system — the null in the last study won 83.6% and still lost money — so a high
win rate is not evidence here and must not be read as any.

**G6 exists because the premium is MODELLED, not observed.** We have no
historical option prices; IV is proxied by VIX, and real short-dated options
trade at a different IV with a skew. If a 15% IV error flips G2, the result is
an artefact of the model rather than a property of the market.

## The honest limits, recorded up front

1. **No real option prices, ever.** Every premium here is Black-Scholes from
   VIX. This is the largest single weakness and no amount of sample fixes it.
2. **The profit target is NOT tested.** The owner's actual edge appears to
   include closing at 30-60% of the credit — on Indian 1-2 DTE data that turned
   a −207 mean into +1,226 with a 100% hit rate over 18 sessions. Testing it
   needs intraday paths; we hold 64 sessions of SPY 5m, and the 3-year backfill
   FAILED on 29 Aug (IBKR auth cooldown, 4,978 of 58,518 bars). **Until that
   lands, any claim about the target is untested.**
3. **The strangle→straddle conversion is not modelled.** The owner: *"dont
   think that conversion can be done automatically by algo"*. Agreed — it is
   judgement, so it is out of scope and its contribution to his live results is
   unmeasured. What is in scope is an ALERT (below).
4. **1 DTE is maximum short gamma.** The worst low-VIX day in ten years was
   −321 against a +20 mean. The day that breaks this will not resemble the 630
   that did not.
5. **SPY is not BANKNIFTY.** The shape transfers; the numbers do not.

## Prediction — recorded before the graded run

**G1, G2, G3, G7 pass.** The exploratory run gave 88.1% win, +20.2 mean, worst
−321 filtered against −4,799 unfiltered: a 15× tail improvement, comfortably
past G7's 3×.

**G5 is the risk.** The 2016-2021 half contains February 2018 (Volmageddon) and
March 2020. Both are exactly the event this structure fears, and a single
session in either could fail G3 for that half. If it does, the honest reading is
that the filter reduces the tail without removing it — which is a real and
useful finding, not a failure to be tuned away.

**G6 I expect to pass at 1.15× and to be tight at 0.85×**: under-collecting
premium by 15% removes most of a +20 mean.

If G3 fails, the answer is NOT a tighter VIX threshold until it passes. It is
that an intraday short strangle carries a tail the filter cannot remove, and
should be sized for that or not traded.

## Scope

Clearing these gates licenses a DAILY EMAILED CANDIDATE and a live alert. It
does not license automated execution, and it does not license the profit target
or the conversion, neither of which is tested here.
