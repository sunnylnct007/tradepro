# Momentum rotation — rebalance frequency and exits: RESULT (16 Nov 2026)

Pre-registered in MOMENTUM_ROTATION_GATES_V2_REBALANCE_EXITS.md (committed
before any run). 142 symbols, 2006-08 -> 2026-11-13, costs 0.1% a trade.

| Variant | Full CAGR | Full DD | Full Sharpe | Trades | 2008-17 Sharpe / CAGR | 2018-now Sharpe / CAGR | Pass |
|---|---|---|---|---|---|---|---|
| **Control: monthly, 20% trail** | **9.68** | **-14.36** | **0.98** | 1532 | 0.81 / 6.93 | 1.11 / 12.46 | — |
| Every two weeks | 7.34 | -18.63 | 0.77 | 2343 | 0.74 / 6.26 | 0.79 / 8.32 | fail |
| Weekly | 8.63 | -15.67 | 0.87 | 3269 | 0.78 / 6.74 | 0.95 / 10.55 | fail |
| Profit target +25% | 7.86 | -13.83 | 0.91 | 1887 | 0.89 / 7.06 | 0.87 / 8.22 | fail |
| Profit target +50% | 9.01 | -13.31 | 0.96 | 1639 | 0.80 / 6.83 | 1.09 / 11.18 | fail |
| Trail 10% once +40% | 9.25 | -12.59 | 0.97 | 1632 | 0.82 / 6.92 | 1.09 / 11.56 | fail |

**Decision: the control stays.** No variant beat it in both halves.

- Rebalancing more often LOSES: more trades (2-3x), lower return, deeper
  drawdown. Every-two-weeks is worst. The ranking on 3/6/12-month returns
  barely moves inside a month; extra rebalances mostly churn names near the
  cut-off and buy short-term winners just before they give some back.
- A +25% profit target cuts the winners that carry the strategy: 2018-now
  CAGR 12.46 -> 8.22. It helped 2008-17 slightly and hurt 2018-now badly.
- +50% target and the tighter trail after +40% cut the drawdown a little
  (-14.4 -> -13.3 / -12.6) for less return in 2018-now. Close, not better.
  Not adopted: the pass mark asked for better in BOTH halves.

No combinations were run (none passed alone). Same data caveat as V1 for all
rows equally.
