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
