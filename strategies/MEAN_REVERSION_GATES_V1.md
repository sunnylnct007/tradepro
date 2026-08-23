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

---

# THE HARNESS DOES NOT EXIST. Read this before citing any number above.

Discovered 22 Aug while trying to reconcile the median-hold figure against an
independent replay. **The script that produced every number in this file was
written in a scratch directory and never committed.** All that survived is
`backtests/logs/mr_sweep.log`.

That log records: sigma, volume filter, target, trades, win%, mean%, total%,
tail share, worst trade, and high-ATR breakdown. It **does not record median
hold at all.**

So the "median hold 4 bars" that satisfied G3, and that the live Swing screen
displays, traces to no surviving artifact. It is not a disputed measurement —
there is nothing to dispute it against. An independent replay gives 8.

## What this means for the re-run

It is **not a reconciliation of two numbers. It is the first reproducible
measurement.** Anything downstream that cited "4 bars" was citing a log line
that does not contain it.

The same caution applies more weakly to the rest of the table: those figures
DO appear in the log, so they were really produced — but they cannot be
re-derived, checked for an off-by-one, or re-run on a corrected universe
without rewriting the harness from the gates description. Which is what the
re-run will do.

## Rule adopted as a result

A study is not finished until its harness is committed to
`strategies/backtests/studies/` alongside its gates file, the gates commit sha,
its log, and its `studies.json` record. Every harness from 22 Aug onward is
there. See `strategies/backtests/README.md`.

---

# v2 RE-RUN — 22 Aug 2026. ALL SIX GATES PASS. Harness committed.

`backtests/studies/mean_reversion_v2.py` · 89-name defined universe · cleaned
store, verified phantom-free · realistic gap fills.

| gate | threshold | v1 | v2 | |
|---|---|---|---|---|
| V0 | >= 1,000 trades | 2,400 | 1,270 | PASS |
| G1 | win >= 55% | 65.5% | **66.2%** | PASS |
| G2 | mean net > 0 | +0.59% | **+0.88%** | PASS |
| G3 | median hold <= 10 | 4 | **7** | PASS |
| **G4** | top-1% share <= 25% | **26% FAIL** | **19.9%** | **PASS** |
| G5 | worst >= -25% | -12.5% | **-17.7%** | PASS |

**G4, the gate v1 failed and shipped anyway, now passes** — 19.9% of net
profit from the top 1% against a 25% ceiling. The open decision flagged
earlier is therefore closed by measurement rather than by argument.

Also passes a **two-split test** that is not a v1 gate, added because it
rejected momentum v3 and the intraday dip study the same day:

| cell | n | win | mean |
|---|---|---|---|
| time 1st half | 633 | 67.0% | +0.74% |
| time 2nd half | 637 | 65.5% | +1.01% |
| symbols even | 629 | 66.9% | +1.04% |
| symbols odd | 641 | 65.5% | +0.71% |

## Why the numbers moved

**Trade count halved** (2,400 → 1,270): v1 ran over a symbol list that included
futures, indices and foreign listings. The universe is now 89 defined names.

**The worst trade got worse** (-12.5% → -17.7%) and this is a correction, not
a deterioration. v2 fills stops at `min(stop, open)` because a gap through a
stop does not fill at the stop. Modelling the fill at the trigger produced
exactly **-8.0% in every variant** — a stop that never slips, which does not
exist. The first version of this harness had that flaw and was corrected
before grading.

**The hold could not be reproduced.** All four exit conventions were run —
target moving vs fixed at entry, filled on the session high vs the close — and
they give 7, 9, 9 and 10 bars. **None gives 4.** v1's hold figure remains
unexplained and is now superseded rather than reconciled.

**G4 was measured both ways** because v1's definition is unrecoverable: top 1%
as a share of WINNING profit is 7.0%, as a share of NET profit 19.9%. The
stricter reading is the one graded.

## Robustness

All four exit conventions pass every gate (tail 19.4-21.3% of net, hold 7-10,
worst -17.7% to -20.4%). The result does not depend on choosing one.

## Status

The Swing screen now publishes these figures with the harness path beside
them. **This is the first number set in the project that can be re-run.**

---

# G4 IS THE SENSITIVE GATE. Expect it to be the one that breaks.

Flagged by the data lane 23 Aug, and correct. Across the two runs:

| universe | trades | G4 (top-1% share of net) | margin to the 25% ceiling |
|---|---|---|---|
| 89 names | 1,270 | 19.9% | 5.1 points |
| 244 names | 2,251 | **21.9%** | **3.1 points** |

G4 moves WITH POPULATION SIZE by construction — a wider universe contains more
names capable of an outsized single winner, so tail concentration rises even
when nothing about the strategy has changed. The direction is explainable and
it still passes. But the margin has shrunk by 40% for a reason that has
nothing to do with the edge.

**So if the universe changes again — a liquidity-floor adjustment, a new
listing set, another data correction that restores excluded names — G4 is the
gate most likely to fail, and such a failure should be read as a population
effect FIRST, not as the strategy degrading.**

Every other gate is population-stable: win rate, mean, hold and worst trade
barely moved across a near-tripling (66.2%→64.9%, +0.88%→+0.85%, 7→7 bars,
-17.7% unchanged). G4 moved 2 points. It is the outlier and it is the one to
watch.

Recorded in advance so a future failure reads as expected rather than as a
surprise — which is the entire purpose of writing gates down before running.
