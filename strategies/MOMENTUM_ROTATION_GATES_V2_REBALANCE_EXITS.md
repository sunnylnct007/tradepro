# Momentum rotation — rebalance frequency and exits (pre-registered, 16 Nov 2026)

Written BEFORE any run. Owner, 16 Nov 2026: "shd we not run rebalance a bit
more frequent", "if we looking for trailing stop shd we not look at max profit
as well", "yes we need to optimize our rebalancing".

## Control

The decided design as it trades on paper today (MOMENTUM_ROTATION_GATES_V1.md,
`Params()` defaults): monthly rebalance on the 15th, 20% trailing stop, no
profit target, 25% vol target, 0.1% cost a trade.

## Variants — each ONE change from the control, all run, all reported

Rebalance frequency (`rebalance_every`):
1. every two weeks (the last session of every second week)
2. weekly (the last session of each week)

Exits:
3. profit target +25% (`take_profit=0.25`)
4. profit target +50% (`take_profit=0.50`)
5. trail tightens to 10% once a holding is +40% above entry
   (`trail_tight_after=0.40, trail_tight=0.10`)

No combinations are run unless two single changes pass on their own.

## Pass mark — a variant replaces the control only if ALL hold

- In BOTH halves, 2008-01-01..2017-12-31 and 2018-01-01..latest:
  Sharpe at least control + 0.05, AND CAGR at least the control's.
- Full period: worst drawdown no more than 2 percentage points deeper.
- Costs included (0.1% of traded value, as V1). Turnover and trades reported.

Anything else keeps the control, and the result is recorded either way in
MOMENTUM_ROTATION_RESULT_V2_REBALANCE_EXITS.md.

## Known limit

The same data as V1, with the same caveat (the store mixes adjusted sources;
some names have short IBKR history). It applies equally to every variant, so
the comparison is fair even where the absolute level is not.
