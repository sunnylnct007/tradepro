# Mean-reversion swing candidates — PRE-REGISTERED gates

**Committed BEFORE the first run** (22 Aug 2026).

## What the owner actually asked for

> *"what I need at least are some symbols where I can get in and get out after
> making some money. I manually executed trades on MU, Google, ACN a couple of
> times and did nicely ... we are more interested in direction, mean reversion
> etc so we get candidates where we get in and get out without holding them
> for longer."*

This is a DIFFERENT STRATEGY FAMILY from everything tested so far, and the
reason the previous work kept failing is now clear:

| | Ichimoku (what we had) | what is wanted |
|---|---|---|
| hold | 41+ bars to pay (median 19-34) | days |
| win rate | 39% ceiling ~44% | high |
| profit shape | top 1% of trades = 54% of profit | steady, repeatable |

Ichimoku's edge IS the long hold and the tail. Trying to make it produce quick
in-and-out trades destroyed it — the entry filters cost 43% of total return, and
no exit variant reached a 45% win rate. That was not a tuning failure; it was
the wrong tool.

Mean reversion has the opposite profile by construction: many small wins, short
holds, high hit rate, and the risk sits in the rare large loss rather than in
missing a rare large win.

## What is being tested

Buy short-term OVERSOLD conditions in names that are in a LONG-TERM UPTREND
(the standard construction — dip-buying strength, not catching knives). Exit
quickly on reversion.

**Entries** (all additionally require `close > 200-SMA`):

| # | Entry |
|---|-------|
| M1 | RSI(2) < 10 |
| M2 | close below lower Bollinger(20, 2σ) |
| M3 | 3 consecutive down closes |
| M4 | close at a 5-day low |

**Exits** (same for all, so entries are compared like-for-like):

| # | Exit |
|---|------|
| X1 | first close > prior close (Connors-style), max 10 bars |
| X2 | close > 5-day SMA, max 10 bars |
| X3 | fixed 5 bars |

Costs 5bps/side, MOO fills (signal on close, fill next open) — same execution
model as every prior study here, so results are comparable.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Trades (validity) | ≥ 1,000 |
| **G1** | **Win rate ≥ 55%** | true |
| **G2** | Mean return per trade, NET of costs | > 0 |
| **G3** | **Median hold ≤ 10 bars** — the owner's actual requirement | true |
| **G4** | Top-1% profit share ≤ 25% — must NOT be tail-dependent | true |
| **G5** | Worst single trade ≥ −25% — the family's known failure mode | true |

**G1 is set at 55%, not 45%.** A mean-reversion system that only wins 45% of
the time is not doing its job — the whole point of the family is a high hit
rate on small gains. Setting the bar at the owner's floor would be too generous
for this construction.

**G4 exists because it is the trap that killed the trend-following work.** A
"mean reversion" result that depends on a few enormous winners is not mean
reversion; it is a trend follower in disguise, and it would fail the owner's
actual requirement.

**G5 is the family's real risk.** Mean reversion wins often and loses rarely but
badly — buying a dip that keeps dipping. A system that clears G1-G4 while
carrying a -60% single trade has simply moved the risk somewhere less visible.

## Prediction — recorded before the work

**M1 (RSI-2 < 10) + X1 passes G1-G4 and is the strongest**; it is the
best-documented construction in this family. **G5 is where I expect trouble** —
the uptrend filter helps but will not prevent every falling knife, and one
2020-style crash in the window could produce a very large single loss.

**M3 (3 down days) I expect to pass but with a thinner edge**; it is the
crudest condition.

If G1 fails across all variants, the honest conclusion is that this universe
and window do not support simple mean reversion, and the answer is NOT to widen
the entry until something passes.

## Scope

This is a CANDIDATE-GENERATION study. Clearing these gates would license
building a daily shortlist surface, not live trading — that needs its own
paper-replay evidence per the standing rule.

---

# RESULT — recorded late, 22 Aug 2026. G4 FAILED and it shipped anyway.

This section should have been written when the study ran. It was not: the
result lived only in `backtests/studies.json` while this file — the
pre-registered contract — was left with no outcome against it. A peer session
reviewing the protocol from outside flagged that a gate looked silently
waived. They had the gate NUMBER wrong; they were right about the substance.

| gate | test | result | |
|---|---|---|---|
| V0 | >= 1,000 trades | 2,400 | PASS |
| G1 | win rate >= 55% | 65.5% | PASS |
| G2 | mean net > 0 | +0.59% | PASS |
| G3 | median hold <= 10 bars | 4 bars | PASS |
| **G4** | **top-1% share <= 25%** | **26%** | **FAIL** |
| G5 | worst trade >= -25% | -12.5% | PASS (see below) |

**The Swing screen shipped on a study that failed a pre-registered gate.**
Momentum v3, analog v1 and intraday dip v1 were all held to "ships only if it
passes EVERY gate". Mean reversion v1 was not. No reasoning for the exception
was recorded anywhere at the time, which is the part that matters: an
unrecorded exception is indistinguishable from the protocol being decorative.

G4 measures tail concentration — how much of the profit comes from the top 1%
of trades. Missing by one point is not the same class of failure as missing on
returns, and there is a defensible argument for shipping. But that argument
was never written down, so it cannot be audited. **This is flagged to the
owner as an open decision, not resolved unilaterally here.**

## Two further problems with the numbers above

1. **G5's -12.5% is wrong.** Measured on the pre-`_tradeable()` population,
   which included futures, indices and foreign listings. An independent replay
   on the tradeable universe puts the worst trade near **-22%** — still inside
   the -25% gate, so G5 still passes, but the margin is a third of what it
   appeared. The screen now shows -22% flagged "under reconciliation".
2. **The universe changed underneath this study.** The bar cache went from 286
   to 250 symbols on 22 Aug (futures, indices, crypto, foreign listings
   removed; LSE ETFs moved to a separate tree), and the defined universe is now
   89 names. **Runs before and after that boundary are not comparable.** Any
   re-run must state which side of it it sits on.

## Consequence

A confirmation re-run is required, and it is step 2 of the current plan. It
must settle three things at once: the G4 verdict on clean data, the true
worst-trade figure, and the median-hold discrepancy (this file says 4 bars;
an independent replay says 8, which means the exit mechanics differ somewhere).

Until then the Swing screen carries its evidence with the tail flagged.
