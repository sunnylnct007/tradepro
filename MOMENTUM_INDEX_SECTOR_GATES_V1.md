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
