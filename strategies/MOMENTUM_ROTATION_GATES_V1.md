# Monthly momentum rotation (core engine): PRE-REGISTERED spec and gates, v1

**Committed before any backtest was run**, 12 Nov 2026. The design decisions are
the owner's (SK, 12 Nov 2026) and are not tuned against results. Items marked
*proposed* are implementation details TradePro may refine; any change to them is
recorded below with its date, before the run that uses it.

Account figures are deliberately absent: this repository is public.

## Purpose

The account's core strategy, planned at about 50% of net liquidation, inside the
charter limits: 25% maximum drawdown, 25% cash reserve, 40-45% sector cap, 12%
single-stock cap. TradePro runs it on PAPER only. SK places every live order.

Prior evidence: a 72-name backtest (2008-2026), monthly top-10 by 6-month return,
above the 200-day, with an SPY 200-day switch, returned about 22.7% a year with a
-36% month-end drawdown against 9.5% for SPY. Survivor-biased and in-sample; the
reviewer expects it to fall, possibly to the low teens, on a fair universe.

## Universe

TradePro's daily-harvest symbols (the `harvest` flag in universe/tradeable.json
plus the owner's toggles on the universes page) that carry a sector label (build
pack, 13 Nov 2026, rule 1). On 13 Nov that is 123 stocks: the ~100
sector-balanced names the owner chose on 12 Nov (9 most liquid per GICS sector)
plus live-book and earlier sector picks. ETFs have no sector label and are
excluded; SPY and QQQ are regime inputs only. A harvested stock with no sector
label is excluded until it gets one.

Eligibility at each rebalance, using data up to that day only:
- at least 252 trading days of history;
- liquidity (*proposed*): close above $10 and median daily traded value above
  $20M over the last 60 days;
- excluded while the symbol fails the harvest's bar checks.

The list is survivor-biased: chosen from today's index by today's liquidity.

## Data convention

Daily bars from the golden store. `close` is raw (split-adjusted);
`close * adj_factor` is the dividend-adjusted price. Returns, the ranking score
and the equity curve use the adjusted price (12 Nov 2026: the store was rewritten
into this single convention first; see SHARED_CONTEXT).

## Rules (decisions 1-7)

1. **Ranking.** Score = simple average of total returns over the last 63, 126 and
   252 trading days, to and including the rebalance-day close. Highest first;
   ties go to the higher 252-day return.
2. **Market filter.** If SPY closes below its 200-day average on the rebalance
   day, sell everything and hold cash until a later rebalance finds SPY above.
   Checked monthly only.
3. **Stock filter** (from the tested design): held only if it closes above its
   own 200-day average on the rebalance day.
4. **Portfolio.** Top 10 eligible names, equal weight. Fewer than 10 qualify: the
   unused slots stay in cash. At most 4 of 10 in one sector.
5. **Volatility target 25%** (20% in the original spec; see the change log).
   Engine allocation = 50% x min(1, 25% / the selected portfolio's annualised
   volatility over the last 63 days, equal weights).
6. **Rebalance** on the close of the 15th, or the last trading day before it;
   orders the next trading day.
7. **Exits.** At the rebalance (dropped from the top 10, fails a filter, breaks a
   cap), or a close 20% below the highest close since purchase (checked daily,
   sold next day, slot in cash until the next rebalance). Account -15% from peak:
   halve the allocation. -25%: all to cash.

Backtest execution (*proposed*): signals on the close; fills at the next trading
day's open; 0.1% cost per trade; sells before buys; holdings that stay are traded
only to bring a weight back within 2 percentage points of target.

## Backtest and robustness plan

1. **Main run**: the rules above, 2008 to now, daily equity, 0.1% costs, USD.
2. **Robustness** (report every variant and how many were run): ranking blend vs
   3, 6, 9, 12 months and 12-minus-1; 8/10/12/15 names; mid-month vs month-end,
   +-5 days, 4 weekly tranches; SPY 200-day vs 10-month vs breadth vs none; vol
   target off/15/20/25% (the decided 25% plus the other three); trailing stop none/15/20/25%; sector cap on/off.
3. **Walk-forward**: choose on 2008-2017, test unchanged on 2018-now; then roll.
4. **Universe check**: the core list has no delisted names, so repeat the main
   run on a point-in-time universe before any live money.
5. **Report per run**: annual return, worst daily drawdown, Sharpe, worst rolling
   12 months, yearly returns, turnover, average holding time, trailing-stop exits,
   time in cash, largest sector weight over time.

## Gate (charter gate 1): ALL must pass

| # | Test | Threshold |
|---|------|-----------|
| G1 | Beats SPY after costs | annual return above SPY's over the same span |
| G2 | Beats equal-weight hold of the same universe | after costs |
| G3 | Drawdown fits the account | worst daily drawdown x 50% inside 25% |
| G4 | Walk-forward | out-of-sample keeps at least half the in-sample return |
| G5 | Robustness | most variants beat SPY |

## Paper run (after the gate passes)

IBKR paper, at least 3 rebalances: rebalance log (targets, scores, filters,
sector weights), paper orders next day with fills, slippage and commissions,
daily trailing-stop and drawdown checks, monthly scorecard against SPY and QQQ,
rule adherence of at least 95%, any override logged with its reason.

## Walk-forward method (fixed 12 Nov 2026, BEFORE the walk-forward run)

- Candidates: the decided design plus the 21 robustness variants (22 in all).
- Selection: the highest Sharpe ratio on the in-sample window.
- Single split: select on 2008-2017, run the pick unchanged on 2018 to now.
  G4 passes if out-of-sample CAGR is at least half the in-sample CAGR.
- Rolling: for each year Y from 2018, select on 2008 to Y-1, trade year Y,
  chain the yearly returns. Report the chained CAGR and which variant each
  year chose.
- Reported for the decided design too, unselected, as the control.

## Change log

- 12 Nov 2026: universe narrowed to ~100 sector-balanced names (owner).
- 13 Nov 2026: universe = harvest symbols with a sector label (build pack rule 1);
  the separate 99-name file is retired so the harvest flag is the one list.
- 13 Nov 2026: build pack rule 5 says a 20% volatility target; the owner kept
  25% ("Keep 25%"), so this file and the code stay at 25%.
- 14 Nov 2026: an outside review of the first live list proposed four rule
  changes. Measured as switches (MOMENTUM_ROTATION_RESULT_V1.md, "Review of the
  first list"); none is distinguishable from noise on this universe. Decision
  (owner, 14 Nov): the 16 Nov orders trade the design AS DECIDED. The 50-day
  entry filter (`entry_above_sma50`) is scheduled as a recorded change from the
  15 Dec 2026 signal; it is switched on before that close, not earlier.
- 12 Nov 2026, **AFTER the v1 results were seen**: volatility target 20% -> 25%
  (owner: "ok 25"). Evidence (account): 20% gave 8.4% CAGR / -13.0% DD, 25% gave
  9.5% / -13.0%, off gave 11.4% / -14.5%; the spec required the target to cut
  drawdown without giving up most of the return, and 20% did not. Because this
  was chosen with the results in view, the backtest cannot validate it; the
  paper run is its first honest test.
