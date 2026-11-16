# Momentum rotation — sector, correlation and per-stock scaling (pre-registered, 16 Nov 2026)

Written BEFORE any run. Owner, 16 Nov 2026, on a tech sell-off with AMD, MU
and DELL held: "this is where our strategy tuning needs to come in place as
diff stocks have diff conditions", "we cannt use same 50 day 200 day logic",
"there is diff correlations we have to consider", "stock having sector sell
off is diff then stock having no sector sell off".

No per-stock parameter fitting: with 142 names that fits noise. Every variant
is ONE rule applied to all names that SCALES to each name's own volatility,
sector or correlations.

## Control

`Params()` as traded today: monthly on the 15th, 3/6/12-month blend, top 10,
SPY 200-day filter, own 200-day filter, 4 per sector, 25% vol target, 20%
trailing stop, 0.1% cost.

## Sector map (SPDR sector ETFs, already harvested)

Industrials XLI, Health Care XLV, Information Technology XLK, Financials XLF,
Communication Services XLC (from 2018-06), Consumer Discretionary XLY,
Consumer Staples XLP, Energy XLE, Real Estate XLRE (from 2015-10), Materials
XLB, Utilities XLU. Where the sector ETF has no history yet, sector rules do
not apply to that name (it is treated as the control would).

## Variants — each ONE change from the control, all run, all reported

Sector context:
1. `sector_etf_filter="sma50"`: at a decision, a name whose sector ETF closes
   below its 50-day average is not eligible (the next-ranked name takes the slot).
2. `sector_etf_filter="sma200"`: the same with the 200-day.
3. `sector_tight_stop=0.10`: while a holding's sector ETF is below its 50-day,
   its trailing stop tightens from 20% to 10%.
4. `relative_stop=0.15`: also exit when the stock's price RELATIVE to its
   sector ETF falls 15% below its best since entry (falling on its own).

Correlation:
5. `corr_cap=0.7` with `sector_cap=None`: picks are taken in score order,
   skipping any name whose 63-day daily-return correlation with an
   already-chosen pick exceeds 0.7 (replaces the label-based sector cap).

Per-stock scaling:
6. `vol_stop=True`: each holding's trailing stop = 2 x its 63-day daily
   volatility x sqrt(21), clipped to 10%-30%, set at entry.
7. `score_mode="vol_adjusted"`: score divided by 126-day volatility.

Recent momentum:
8. `min_ret_3m=0.0`: a name needs a positive 3-month return to be eligible.
9. `skip_recent=21`: the blend skips the most recent month (12-minus-1 style).

No combinations unless two single changes pass on their own.

## Pass mark — unchanged from V2

In BOTH halves (2008-01-01..2017-12-31 and 2018-01-01..latest): Sharpe at
least control + 0.05 AND CAGR at least the control's; full-period worst
drawdown no more than 2 points deeper. Nine variants are run, so one passing
by luck is plausible: a pass is a candidate for a forward paper comparison,
not an automatic switch.

Result recorded in MOMENTUM_ROTATION_RESULT_V3_SECTOR_CORRELATION.md either way.
