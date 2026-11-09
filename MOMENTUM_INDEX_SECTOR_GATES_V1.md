# Momentum + index and sector filter — does market context add anything?

**Status: PRE-REGISTERED. Committed BEFORE the run.**
Supersedes nothing. v2 (`MOMENTUM_GATES_V2.md`) stays live on the Momentum
screen whatever this run says — see "What a pass means" below.

## Why this study exists

Owner, 9 Nov 2026, on seeing the momentum paper books losing money: *"if we
correlate that with future and options pricing and index pricing that should
be a killer combination"* (OWNER BASICS, observation O5, and basic 7: use
sector and exchange indexes, not stock price alone).

The v2 rule reads only the stock's own price. `get_strategy_rules` says so in
its own words: *no relative strength vs SPY, no ADX, no volume condition*. On
13-16 Oct 2026 the T212 momentum book opened 30 positions in four sessions
and nothing in the rule asked what the market or the sectors were doing.

This is the INDEX AND SECTOR leg of O5 only. Options pricing has too little
harvested history to test yet, and the account has no futures data.

## Where this hypothesis came from (it matters)

Unlike v3's volume filter, this was NOT found by slicing a sample whose
answer had been seen. It is a prior, stated by the owner before any test.
That makes it a cleaner test than v3. It does not make it out-of-sample:
there is still no untouched hold-out, and the two-way split gate (G7) is kept
for that reason.

## Definitions — one definition of "uptrend", reused

`uptrend(X, t)` := `close > SMA(200)` AND `SMA(20) > SMA(50)`, on X's own
closes up to and including bar t. This is the trend half of the v2 stock rule
applied unchanged to the benchmark. No new constant is introduced.

`stronger(S, X, t)` := S's 63-session return to t is greater than X's.
63 sessions is the lookback `relative_context.py` already displays.

Market = SPY. Sector = the SPDR sector ETF for the stock's GICS sector in
`strategies/universe/tradeable.json` (XLK, XLF, XLV, XLI, XLY, XLP, XLU,
XLRE, XLB, XLC, XLE). **Fallback, fixed now:** if a stock has no GICS sector,
or its sector ETF has fewer than 210 bars at t (XLC before 2019, XLRE before
2016), the sector leg uses SPY. A benchmark with no bar on date t uses its
last bar before t. Nothing is ever read from after t.

## Variants

All identical to v2 except for an added condition evaluated on the entry bar:

* **A — control**: v2 exactly.
* **B — market trend**: `uptrend(SPY)`.
* **C — market + sector trend**: `uptrend(SPY)` AND `uptrend(sector)`.
* **D — relative strength**: `stronger(stock, SPY)` AND `stronger(stock, sector)`.
* **E — all (the owner's combination)**: C AND D.

E is the hypothesis. B, C and D exist to show which leg does the work.

As in v3, variants are SUBSETS of the control's trade list (a blocked entry
does not free the symbol for a different, later entry), so all five are
compared on one trade population. Exits are unchanged: hard -8% stop, 8%
trail from the peak, 60-session timeout, >35% single-session corrupt-bar
discard.

## Data and universe — the limits of THIS run, stated up front

This run is a FIRST PASS, executed from a cloud session that cannot reach the
golden bar store on the Mac. Therefore:

1. **Bars come from the TradePro `get_candles` endpoint** (vendor daily bars,
   2006-01-01 onward, `adjustedClose` used for every calculation), NOT from
   the golden parquet store the gated studies read. The control will not
   reproduce the published v2 numbers exactly and is not expected to.
2. **Universe is the 79 stocks** in `tradeable.json` (as of 2026-11-09) that
   are `harvest: true` or tagged `large_50`, minus the 31 ETFs. That is the
   live core, not the 956-name screen.
3. GICS sectors are TODAY's classification applied to the whole history.
4. The universe is today's survivors. That flatters every variant including
   the control, so the comparison stands but the absolute numbers do not.

Only SPY had been fetched when this file was committed. No stock series had
been loaded and no variant had been computed.

## Gates

Each gate compares a variant with the control from the SAME run.

| # | Gate | Threshold | Why |
|---|---|---|---|
| G1 | Trades kept | >= 40% of control | A filter that removes most of the screen is a different, thinner product. |
| G2 | Mean per trade | >= control + 0.30 pts | The lift has to be worth carrying a second data dependency. |
| G3 | Win rate | >= control + 1.5 pts | The claimed improvement must appear in the typical trade, not only the tail. |
| G4 | Median per trade | >= control + 0.20 pts | v2's median trade loses money; that is the thing worth fixing. |
| G5 | Worst single trade | no worse than control | No fatter tail. |
| G6 | **Bad months** | 10th-percentile entry-month mean >= control + 1.0 pt | This is the owner's actual complaint: clusters of entries just before a selloff. Months = calendar month of entry with >= 5 control entries; a month where the variant takes no trade scores 0.0 (flat). |
| G7 | **Survives both splits** | mean AND win rate beat control in all 4 cells | Time split at the median entry date, and alternating-symbol split. The anti-curve-fit gate. |

## What a pass means — and does not

* **All gates pass** -> the variant is a CANDIDATE. It earns a confirmation
  run on the golden store over the full universe, with these same gates. It
  does not ship from this file.
* **Any gate fails** -> rejected at first pass. Recorded as a failed study.
  A re-run on the golden store is the owner's call, not automatic.

The October 2026 replay below is DESCRIPTIVE and is not a gate: one episode
cannot grade a filter.

## October 2026 replay (descriptive)

For control entries dated 2026-10-01 to 2026-10-31: how many each variant
would have blocked, and the mean return of blocked versus kept entries
(closed trades at their exit, open ones marked at the last bar).

## Prediction, on record, before running

1. **B (market trend) barely changes anything and FAILS G2.** A stock above
   its own 200-day with 20 > 50 already implies a rising market most of the
   time. I expect B to keep more than 80% of trades and lift the mean by
   under 0.15 pts.
2. **C is B with a little more bite** and also fails G2.
3. **D (relative strength) moves the numbers most**, lifting the mean, but
   keeps or worsens the worst trade (stronger names gap harder), so it is
   at risk on G5, and I put its chance on G7 at about 40%.
4. **E passes G1** (keeps 40-55% of trades) and I put the chance that E
   passes every gate at **under 30%**. G7 is the likeliest killer.
5. **October 2026: the trend filters would NOT have blocked most of the
   13-16 Oct entries**, because the market was still in an uptrend by this
   definition when they were taken. If so, the filter does not address the
   episode that prompted it, whatever its long-run numbers say.

If nothing passes, v2 stays exactly as it is and this file is kept as a
failed study.

---

# RESULT — REJECTED at first pass. No variant passes every gate. v2 stays.

Run 2026-11-09 with `strategies/backtests/studies/momentum_index_sector_v1.py`
on `get_candles` daily bars (adjusted closes), 2006-01-03 to 2026-11-06.
79 stocks · 5,035 sessions · 3,942 closed control trades · time split
2017-09-08 · 225 entry-months in the bad-month gate.

| variant | trades | kept | win% | mean% | median% | worst% | hold | p10 month% |
|---|---|---|---|---|---|---|---|---|
| A control | 3,942 | 100% | 47.5% | +2.22% | -0.70% | -33.7% | 37 | -4.67% |
| B market trend | 2,992 | 76% | 48.8% | **+2.57%** | -0.34% | -33.7% | 40 | -4.30% |
| C market + sector | 2,623 | 67% | 48.2% | +2.36% | -0.51% | -33.7% | 39 | -4.30% |
| D relative strength | 2,558 | 65% | 47.4% | +2.29% | -0.70% | -33.7% | 34 | -4.85% |
| E all | 1,575 | 39.95% | 48.1% | +2.56% | -0.51% | -33.7% | 35 | -4.38% |

Sanity check on the control: the golden-store v2 control in
`MOMENTUM_GATES_V3.md` is +2.20%/trade, 48.8% win, median -0.33%, hold 35.
This run's control is +2.22%, 47.5%, -0.70%, 37 on different bars and a
different universe. Same rule, same shape. The comparison between variants
is therefore believable; the absolute numbers are still not the screen's.

## Gate by gate

| variant | fails |
|---|---|
| B market trend | G3 (win +1.3 pts, needs +1.5), G6 (bad months +0.37 pt, needs +1.0) |
| C market + sector | G2, G3, G4, G6, G7 |
| D relative strength | G2, G3, G4, G6, G7 |
| E all | G1 (39.95% kept, needs 40%), G3, G4, G6, G7 |

## What the four-cell split says

| variant | time 1st half | time 2nd half | symbols even | symbols odd |
|---|---|---|---|---|
| B | PASS +2.74 vs +2.33 | PASS +2.40 vs +2.10 | PASS +2.77 vs +2.46 | PASS +2.36 vs +1.98 |
| C | PASS +2.63 vs +2.33 | **FAIL** +2.09 vs +2.10 | **FAIL** +2.45 vs +2.46 | PASS +2.27 vs +1.98 |
| D | **FAIL** +2.31 vs +2.33 | **FAIL** (win 43.6 vs 43.9) | **FAIL** (win tie) | **FAIL** +1.98 vs +1.98 |
| E | PASS +2.90 vs +2.33 | **FAIL** (win 42.8 vs 43.9) | PASS +2.80 vs +2.46 | PASS +2.28 vs +1.98 |

1. **The plain market-trend filter (B) is the only leg with a consistent
   effect.** +0.35 pts per trade, better in both halves of time and both
   halves of the symbol list. It is real and it is modest.
2. **Adding the sector ETF's trend (C) takes the benefit away again.** It
   removes 369 more trades and the mean falls from +2.57% to +2.36%.
3. **Relative strength (D) is noise here.** It is indistinguishable from the
   control in every cell.
4. **Nothing fixes bad months.** The 10th-percentile entry-month moves from
   -4.67% to -4.30% at best. That was the owner's actual complaint, and it is
   the gate every variant fails.
5. **Nothing changes the worst trade** (-33.7% in all five): the tail is
   overnight gap risk, as already documented for v2.

## October 2026 replay (descriptive)

30 control entries in October on this universe, mean -2.70%.

| variant | blocked | kept mean | blocked mean |
|---|---|---|---|
| B market trend | 0 of 30 | -2.70% | n/a |
| C market + sector | 14 of 30 | -4.72% | -0.38% |
| D relative strength | 11 of 30 | -3.54% | -1.24% |
| E all | 16 of 30 | -4.69% | -0.95% |

The market filter would not have blocked one October entry: the market was
still in an uptrend by this definition. The sector filter blocked nearly half
of them, and **the ones it blocked did better than the ones it kept**. In
the episode that prompted this study the filters would have made the book
worse, not better. Only 5 of the control's October entries fall on 13-16 Oct,
because this universe is 79 names and the T212 book drew from a wider one.

## Prediction grading (predictions committed at e975a3f)

| # | Predicted | Actual | |
|---|---|---|---|
| 1 | B barely matters: keeps >80%, lift <0.15, fails G2 | kept 76%, lift +0.35, PASSED G2; the best variant | **wrong** |
| 2 | C fails G2 | lift +0.14, fails G2 | **right** |
| 3 | D moves the numbers most | D did nothing (+0.07) | **wrong** |
| 4 | E passes G1; under 30% chance of passing everything; G7 the killer | E missed G1 by 0.05 pt; failed; G7 failed | **right on the verdict, wrong on G1** |
| 5 | Trend filters would not block the October entries | market filter blocked 0 of 30; sector filter blocked 14, the wrong ones | **right on the market leg** |

## Deviation from the plan, recorded

`tradeable.json` changed on `main` between pre-registration and the run: four
names (CRDO, CRWV, SKHY, SNDK) became `harvest: true`, so the script's
universe rule now selects 83. Bars had been fetched for the 79 pre-registered
names only, so the run is on those 79 exactly as registered. The four are
listed as missing in the output, not silently dropped.

## Consequence

1. **No index or sector filter ships.** The Momentum screen keeps v2.
2. Owner observation O5, index-and-sector leg: **NOT SUPPORTED at first
   pass.** The "killer combination" (E) is not better than a plain market
   filter, and neither fixes bad months.
3. B is the one thing worth a confirmation run on the golden store over the
   full universe, with these same gates. That is the owner's call.
4. The options-pricing and futures legs of O5 remain untested.
