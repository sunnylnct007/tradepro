# Swing hold period — 10 sessions to 20. Amendment, pre-registered.

**Committed BEFORE the forward test starts (2026-08-24).** The rule is being
changed the night before, and that requires a written justification, not a
quiet edit.

## The finding

Owner asked: *"what if I not hold for 10 sessions but let it fill the limit I
want to trade on like GTC."*

| exit rule | trades | win% | per trade | per day held | med hold | max hold | timeouts |
|---|---|---|---|---|---|---|---|
| **10 sessions (shipped)** | 2,312 | 64.6% | +0.71% | 0.1026% | 7 | 10 | **31.4%** |
| 20 sessions | 2,308 | **72.5%** | **+0.97%** | 0.1166% | 7 | 20 | 2.4% |
| 40 sessions | 2,306 | 72.6% | +0.98% | 0.1168% | 7 | 32 | 0.1% |
| GTC, no timeout | 2,303 | 72.7% | +0.99% | 0.1173% | 7 | 32 | 0% |

**The 10-session timeout was closing 31.4% of trades before they resolved** —
booking a result on positions where neither the target nor the stop had been
reached. It is the one exit that ends a trade which has not actually failed.

## Why 20 and not GTC

20 sessions captures essentially the whole gain (+0.97% of the +0.99%) while
keeping a hard bound on how long capital is committed. True GTC differs by
0.02%/trade and gives up the bound in exchange. Max observed hold is 32
sessions and only 3 trades in sixteen years never resolved at all, so the
bound is nearly free.

Median hold stays **7 sessions** at every setting — the timeout was never
affecting the typical trade, only truncating the tail.

## Evidence this is not curve-fitting

It survives the two-split test that rejected momentum v3, the intraday dip
study, and would have caught both on the full sample. The gain holds in
**all four cells**, and unusually evenly:

| cell | n | 10-sess | 20-sess | gain |
|---|---|---|---|---|
| time 1st half | 1,151 | +0.57% | +0.82% | **+0.25%** |
| time 2nd half | 1,157 | +0.84% | +1.12% | **+0.29%** |
| symbols even | 1,152 | +0.84% | +1.12% | **+0.28%** |
| symbols odd | 1,156 | +0.57% | +0.82% | **+0.25%** |

A curve-fit gain concentrates in one cell. This one does not vary by more than
four basis points across four independent cuts.

## Why change it the night before rather than after

The alternative is knowingly running the inferior rule for twelve weeks, which
spends the whole forward-test window measuring something we have already shown
is worse. The change is backtested on the same 2,300 trades, validated on the
same splits, and the test has not started.

**What this costs:** the forward test now validates a rule that has never been
paper-traded, rather than one that has also never been paper-traded. Nothing
is lost, because nothing had run yet.

## Revised live expectations

    entry at the next open, 20-session cap
    ~72% win · +0.97%/trade backtested · median hold 7 sessions · max 20

The live baseline stays 0.09%/trade below the backtest for the entry-timing
delay, so expect **+0.97%/trade** against the +1.06% backtest.

This paragraph said "+0.88%, not +0.97%" until 24 Aug — the delay deducted
from a figure it had already been deducted from. +0.88% was right when the
backtest stood at +0.97% under the 10-session hold; it became a stale
subtrahend the moment the hold changed, and it contradicted the graded result
printed a few lines above it in this same file.

`FORWARD_TEST_GATES_V1.md` gate F6 (>=15 completed trades) is unaffected — the
signal rate is unchanged at ~7/week; only the exits move.


---

# GRADED RESULT — all six gates pass, and the harness had a bug

Re-graded through `backtests/studies/mean_reversion_v2.py` after fixing a
defect the change itself exposed.

**The harness hardcoded its own `MAX_HOLD = 10`** instead of importing it, so
raising the constant in `signals/mean_reversion.py` never reached it. It kept
grading the old rule and appeared to CONTRADICT the result that motivated the
change — 64.6%/+0.80% against the comparison's 72.5%/+0.97%. That looked like
a real disagreement and was a duplicated constant. Same drift this session
already chased through `poison_check`, the strategy registry and the entry
rule. The harness now imports SIGMA, BB_WINDOW, STOP_PCT and MAX_HOLD.

Isolating one variable at a time first confirmed 20 beats 10 on EVERY
convention, so the direction never depended on the choice:

| entry | hold 10 | hold 20 |
|---|---|---|
| signal close | 64.6% / +0.80% | **72.8% / +1.06%** |
| next open, same-day exit allowed | 64.6% / +0.71% | **72.5% / +0.97%** |
| next open, same-day exit blocked | 65.0% / +0.76% | **72.9% / +1.03%** |

## Graded, 20-session hold

| gate | threshold | result | |
|---|---|---|---|
| V0 | >= 1,000 trades | 2,310 | PASS |
| G1 | win >= 55% | **72.8%** | PASS |
| G2 | mean net > 0 | **+1.06%** | PASS |
| G3 | median hold <= 10 | 7 | PASS |
| G4 | top-1% share <= 25% | **18.2%** | PASS |
| G5 | worst >= -25% | **-23.9%** | PASS |

Two-split passes in all four cells (70.0-75.6% win, +0.91% to +1.22%).

**G5 is the one that moved against us**: -17.7% to -23.9%. A longer hold means
more nights held, and more chances to gap through the stop. It still clears
the -25% gate but the margin is now 1.1 points, so G5 joins G4 as a gate to
watch if anything else changes.

**Revised live expectation: about +0.97%/trade**, being +1.06% less the
~0.09% entry-timing delay.

---

# The two-split test was WEAKER than claimed. Clean version below — it passes.

Prompted by the data lane finding that store history depth is uneven: first-bar
dates cluster at FETCH WINDOWS, not inceptions — 2022-01-03 (90 symbols),
2010-01-04 (73), 2019-07-01 (45), 2021-08-23 (10).

**Consequence I had not accounted for: the two-split's halves are different
universes.** 106 distinct symbols contributed to the first half, 239 to the
second. So the "time split" could not separate *the edge persisted* from
*the universe changed*, and I had been citing it as the strongest evidence
this result was not curve-fitted.

## The clean test — same symbols, different decades

Restricted to the 74 symbols with history before 2013, all of which traded in
both eras:

| era | trades | symbols | win% | mean% |
|---|---|---|---|---|
| before 2019-01-01 | 841 | 74 | **77.1%** | **+0.91%** |
| 2019-01-01 onward | 737 | 74 | **75.2%** | **+1.12%** |

**PASS** — the edge is present in both decades on an identical symbol set,
and slightly stronger in the recent one. This is stronger evidence than the
confounded split it replaces, not weaker.

## Year by year, with the population that produced it

    2013  110 trades / 59 symbols  +1.70%      2020  129 / 80   +0.00%
    2016  119 / 62  +1.45%                     2021  240 / 110  +1.42%
    2017  107 / 58  +1.36%                     2022   88 / 63   -1.07%  <- bear year
    2018  114 / 65  +1.15%                     2024  272 / 168  +1.79%
    2019  107 / 62  +1.03%                     2026  184 / 128  +2.31%

**2022 is the only losing year** and it is the bear market — consistent with
the regime finding that the edge thins when the market breaks. Worth knowing
that a losing year is a normal outcome for this strategy, not a failure of it.

## Carried into the test as a known limitation

XLC and XLRE hold only ~5 years in the canonical store (from 2021-08-23) where
others hold ~16 — also a fetch window, not inception. Fine for sector context
and a 200-SMA; **not fine for any backtest starting before Aug 2021**, which
will silently omit those sectors. The sector-agreement finding from 23 Aug is
therefore recent-weighted for the 12 symbols assigned to XLC and must be
re-measured on filled history before it is acted on.

Re-seeding is deliberately PARKED until the forward test window closes: deeper
history changes the population, and G4 moves with population size while G5 now
has 1.1 points of slack.

---

# History depth is a FETCH WINDOW, and there are two of them. 24 Aug.

Diagnosed by the data lane (`0cdb7cc`) after I asked why the Scanner and the
backtests were reading different depths for the same symbol. Recorded here
because this file is where per-symbol numbers get quoted.

**Mechanism 1 — an unmeasured cap that RE-APPLIES.**
`bar_cache/providers/ibkr_web_provider.max_history()` returns `365*5` days for
any resolution not in its measured table, and `"1d"` is not in that table.
Earliest reachable is therefore 2021-08-25 — which is exactly where the
2021-08-23 first-bar cluster (11 symbols, XLC and XLRE among them) sits. The
other entries in that table carry their evidence in the comments (*"worked at
6 months, failed at 12"*); this one does not. Our own C# path has no such
limit: `IBKRDailyBackfillService` runs a 15-year backfill and pages backward,
so IBKR serves that depth every night. Only the Python side declines to ask.
**A backfill that does not raise `max_history` first will silently truncate to
five years again.**

**Mechanism 2 — old `--from 2010` seed windows.** SPY, AAPL, MSFT, NVDA, QQQ,
MU, KLAC, GOOGL and IWM hold 4,185 bars from 2010-01-04 against 5,000 from
2006-10-05 available — 84%. Not the cap; a stale seed. Raising `max_history`
alone leaves the mega-caps short.

**Caveat on "available":** several symbols return exactly 5,000, the API's own
per-request cap. Measured availability is a FLOOR, not the true depth.

## What this does and does not do to the numbers here

For the 2010 group it is a clean truncation — the evidence is computed on less
data than we hold, not on wrong data.

**For the cap group it is more than that, and I had it wrong first time.** A
symbol whose local history starts 2022-01-03 contains no 2020 crash and no
2022 bear market — the single losing year this strategy has. Its per-symbol
win rate is measured on a materially different regime mix from one starting
2006, so it is not merely "less data". The data lane made this correction to
my framing and it is right.

**Consequence, stated so it cannot be quietly forgotten:** any per-symbol
number — the Scanner's "beats avg" label included — is conditioned on that
symbol's history depth. The Scanner now prints the first-bar date and flags
records that begin after 2021 with what they are missing. The UNIVERSE-level
result above is unaffected: it is 2,310 trades pooled across 244 names, and
the 74-symbol clean split covers both decades on an identical symbol set.

Re-seeding stays PARKED until the forward-test window closes, for the reason
already given: deeper history changes the population, G4 moves with population
size, and G5 now has 1.1 points of slack. Queued for the post-window store
session, which folds this in with the adj_factor/close convention and the
XLC-XLRE depth so the store is opened once rather than four times.

---

# The adjusted/raw close seam does NOT change the verdict. Measured, 25 Aug.

The data lane measured (12a14c5) that the parquet store mixes close
conventions: yfinance-sourced rows are ADJUSTED and sit 8-14% below the raw API
close, while ibkr_web rows agree with it to 0.000%.

Price is what this rule reads, so that had to be checked rather than assumed.

**The exposure is real.** 160 of the 244 universe symbols carry a >=5% one-day
close jump coinciding with a change of source — 893 such steps. HYG, a bond
ETF, shows 19 of them with 18 sign flips, alternating at month-partition
boundaries. A bond ETF does not move 30% in a day; that is a convention
flip-flop, not price.

**The reach into the graded result is much smaller: 104 of 3,636 entry signals,
2.9%**, because the seams sit on month boundaries and only some 20-day windows
straddle one that also stepped.

Re-graded with every seam-contaminated signal excluded
(`backtests/studies/mean_reversion_seam_v1.py`, pre-registered prediction in
its docstring):

| | with seams | seams excluded |
|---|---|---|
| trades | 2,503 | 2,429 |
| G1 win | 73.2% | 73.3% |
| G2 mean | +1.10% | +1.05% |
| G3 hold | 7 | 7 |
| G4 tail | 17.8% | 17.9% |
| G5 worst | -23.2% | -23.2% |

**All six gates hold, and the two-split passes in all four cells both ways.**

I predicted in advance that if anything moved it would be G4 or G5, the
extreme-value gates, since a 21.6% artificial step is the shape that
manufactures an outlier and G5's margin is only 1.8 points. **Those two moved
least** — G4 by 0.1 of a point, G5 not at all. The worst trade is -23.2% in
both runs, so that figure is not a convention artefact, which was the specific
thing worth ruling out.

What the seams actually did is duller: flattered the mean by about five basis
points a trade. Worth removing when the store is repaired; not worth alarm, and
not a reason to stop the forward test.

## Footnote: the baseline moved this morning, and not because of the rule

Today's run is **2,503 trades / 73.2% / +1.10%** where this document records
2,310 / 72.8% / +1.06%. The store moved underneath the harness: 158
de-duplicated partitions, and all 244 August daily partitions re-sourced after
the corrupt ibkr_web writes. The direction is favourable and no gate changes,
which is the only reason this is a footnote. **Any future re-grade should
expect 2,503/+1.10%, not the recorded figures.**

---

# The 1,038 bad `ibkr` closes do not change the verdict either. 25 Aug.

The data lane isolated (a883dc0) a scatter of individually wrong closes: 1,038
rows disagreeing with the API by >1%, 97 by >5%, worst APP 2025-02-12 at a
local 490.75 against an api 380.32 — 29% wrong. **Every one is
`source == "ibkr"`, the retired socket path; zero from `ibkr_web`.** Medians
~0.00%, so this is not a convention seam — it is the TXN class of bad write,
historical and far more numerous.

This mattered more than the seam. A 29% wrong close inside a 20-day window is
exactly the shape that manufactures a 2.5σ trigger out of nothing — which is
what the corrupt TXN bar did on the live screen this morning.

I do not hold the API comparison, so I could not exclude the 1,038 rows
specifically. Instead I excluded **every `ibkr`-sourced bar** — a strict
superset: 84,555 of the universe's 571,254 daily bars, touching 409 of 3,636
entry signals (11.2%, against the seam's 2.9%).

| | baseline | seams excluded | whole `ibkr` provider excluded |
|---|---|---|---|
| trades | 2,503 | 2,429 | 2,205 |
| G1 win | 73.2% | 73.3% | **74.1%** |
| G2 mean | +1.10% | +1.05% | +1.05% |
| G3 hold | 7 | 7 | 7 |
| G4 tail | 17.8% | 17.9% | 18.5% |
| G5 worst | −23.2% | −23.2% | **−23.2%** |

**All six gates survive deleting the entire provider**, two-split passes in all
four cells in every run.

**My prediction was half right and is recorded that way.** I said wrong closes
should have suppressed trades rather than flattered them, and that a
better-than-baseline result would be the seam mechanism repeating. The win rate
does rise (73.2% → 74.1%). The mean falls (+1.10% → +1.05%). Mixed, not the
clean confirmation I set up — and the honest reading is that individually wrong
values err in both directions, unlike the seam's consistent offset, so they
have no single coherent effect to predict.

**The finding worth keeping is G5.** The worst trade is −23.2% in all three
runs — full store, seams removed, an entire provider removed — and does not
move by a basis point. That was the number I was most worried about: it clears
its gate by only 1.8 points, and one bad close is exactly what could
manufacture it. It is now triple-confirmed as a property of the STRATEGY, not
the data. A −23.2% worst trade is what a −8% stop does when a position gaps
through it.

**This is a bound, not a vindication.** 83,517 good bars were deleted to remove
1,038 bad ones, so a failure would not have proved the bad closes caused it.
The repair is still worth doing. It is not urgent, and it is not a reason to
pause the forward test.


---

# CORRECTION: G5's -23.2% was a DATA ARTEFACT. The real worst trade is -17.7%.

Written 25 Aug 2026, correcting my own study from earlier the same day.

I claimed the -23.2% worst trade was "triple-confirmed as a property of the
STRATEGY rather than the data" because it held across three runs. **That was
wrong, and the error was in my filter, not the data.**

`mean_reversion_seam_v1` excluded signals whose **20-day SIGNAL WINDOW**
contained a convention seam. It never examined the **HOLDING PERIOD** — where
the exit happens, and therefore where a phantom bar decides what a trade is
worth. I filtered the half of the trade that produces the signal and ignored
the half that produces the result.

The worst trade in the whole backtest is HYG, signal 2021-11-26. HYG is a bond
ETF that moved +/-0.5% a day all that month:

    2021-11-26   85.47   ibkr_web   <- signal
    2021-11-29   86.00   ibkr_web
    2021-11-30   65.42   yfinance   -23.9%   <- one bar
    2021-12-01   85.37   ibkr_web   +30.5%

One yfinance bar at 65.42 between ibkr_web bars at 86.00 and 85.37. The -8%
stop is "gapped through" by the phantom and fills at min(stop, open) = -23.2%.
It sits at i+4 — inside the holding period, outside the signal window — so my
filter passed it through every time. **Three confirmations of the same blind
spot are not three confirmations.**

My falsification test was blind too. `mean_reversion_corrected_v1` said "if a
corrected close changes the worst trade, -23.2% was an artefact". It did not
change — because the data lane's manifest covers `source == "ibkr"` rows and
HYG's phantom is a `yfinance` row. I designed the right test and pointed it at
a dataset that could not contain the fault.

## Re-graded with the whole trade path filtered

| | baseline | signal window only | **whole path** |
|---|---|---|---|
| trades | 2,503 | 2,429 | 2,385 |
| G1 win | 73.2% | 73.3% | 74.0% |
| G2 mean | +1.10% | +1.05% | +1.13% |
| G3 hold | 7 | 7 | 7 |
| G4 tail | 17.8% | 17.9% | **16.0%** |
| G5 worst | -23.2% | -23.2% | **-17.7%** |

All six still PASS, two-split passes in all four cells.

**G5's true margin is 7.3 points, not 1.8.** Every warning in this document
that "G5 is now a gate to watch, the margin is only 1.1 points" was sized off a
fabricated number and should be read as withdrawn. The strategy is SAFER than
reported.

The real worst trade is **-17.7%, HOOD on 2024-08-02** — a genuine selloff in a
volatile name, and the same figure this rule recorded on the 89-name universe
before the hold change. The clean tail is consistent across two independent
universes.

## What survives

The mean and the win rate do not move under ANY of the four filters. The
central claim — that the edge is real and not a data artefact — holds
throughout. Only the TAIL was contaminated, and the tail is one gate.

## The invariant this needed

Not "check more carefully" — I checked three times, and each check asked the
same question, because that is where my model of the rule lives. The reusable
form is:

> **A trade result must be explainable by bars that are themselves consistent.**
> Not "is the signal clean" but "is every bar this trade TOUCHED clean" —
> entry, exit, and everything in between.

Checkable without knowing what is wrong with the data, which is the property
that makes it worth having.
