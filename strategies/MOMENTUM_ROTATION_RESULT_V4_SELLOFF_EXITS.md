# Momentum rotation — sell-off exits: RESULT (21 Nov 2026)

Pre-registered in MOMENTUM_ROTATION_GATES_V4_SELLOFF_EXITS.md (c462427f,
committed before any run). 142 symbols, 2008-01 -> 2026-11-20, costs 0.1% a
trade. Whole account (the engine invests at most 50%; the rest is cash).

| Variant | CAGR | Worst DD | Sharpe | 2008-17 Sharpe / CAGR | 2018-now Sharpe / CAGR | Trades | Exits | Time in cash | Pass |
|---|---|---|---|---|---|---|---|---|---|
| **Control (market checked at the monthly decision only)** | **9.51** | **-14.36** | **0.96** | 0.81 / 6.93 | 1.08 / 12.06 | 1541 | 0 | 22% | — |
| SPY below 200-day, daily | 8.38 | -16.74 | 0.90 | 0.84 / 6.94 | 0.92 / 9.60 | 1653 | 20 | 27% | fail |
| SPY 3% below 200-day, daily | 8.88 | -14.10 | 0.91 | 0.78 / 6.54 | 1.01 / 11.16 | 1559 | 11 | 24% | fail |
| SPY below 200-day, weekly | 8.32 | -15.02 | 0.88 | 0.76 / 6.32 | 0.96 / 10.20 | 1605 | 15 | 26% | fail |
| SPY below 50-day, daily | 6.42 | **-11.77** | 0.85 | 0.80 / 5.12 | 0.88 / 7.52 | 2433 | 95 | 51% | fail |

Account return through each sell-off window (descriptive, %):

| Variant | 2008-09..09-03 | 2011 | 2015-16 | 2018 Q4 | 2020 Covid | 2022 | Nov 2026 |
|---|---|---|---|---|---|---|---|
| Control | 0.0 | -8.4 | -1.7 | -8.5 | -9.8 | -3.7 | -2.8 |
| SPY < 200d daily | 0.0 | -4.2 | -2.7 | -6.2 | -6.9 | **-10.2** | -2.0 |
| SPY 3% < 200d | 0.0 | -5.9 | -6.5 | -8.3 | -8.9 | -7.1 | -2.8 |
| SPY < 200d weekly | 0.0 | -8.0 | -6.5 | -7.9 | -4.9 | -10.1 | -2.8 |
| **SPY < 50d daily** | 0.0 | **+0.2** | **-0.2** | -4.6 | **+0.9** | **-0.4** | **-0.2** |

(2008-09 is 0.0 everywhere: the decision-time filter already had the book in
cash before the crash.)

**Decision: the control stays.** No variant met the mark in both halves.

- The 200-day exits do not help. They sell after the market has already
  fallen, then sit out the rebound until the next decision; in 2022 the daily
  version was whipsawed in and out and did nearly three times worse (-10.2%
  vs -3.7%). Cheaper insurance than it looks is not insurance at all.
- The 50-day exit is the one that HANDLES sell-offs: flat through 2011, 2015,
  2020, 2022 and this month, worst drawdown -11.8% vs -14.4%. It pays for that
  with half its time in cash and about 3 points a year of return (6.4% vs
  9.5%) — it misses the start of every recovery. It is a defensive strategy
  with a different job, not a better version of this one.
- Per the pre-registration, it fails the mark but improves drawdown
  materially, so it earns a FORWARD shadow record, not a switch.
