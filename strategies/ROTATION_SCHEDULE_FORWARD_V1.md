# Rotation rebalance schedule — forward record, V1

Written 17 Nov 2026, before any forward result exists. Owner, same day:
"rebalancing per month seems extreme but if that is better then what can we do".

## What is recorded

The momentum rotation at three rebalance schedules, recorded every session in the
daily evaluation (`daily_evaluation.rotation_schedules`). Nothing places orders.

| Schedule | What it is |
|---|---|
| monthly  | the live rule (MOMENTUM_ROTATION_GATES_V1); also a model of the live book |
| biweekly | the last session of every second completed week |
| weekly   | the last session of every completed week |

All three are the backtest engine itself (`momentum_rotation.backtest`): the same
`select()`, the 20% trailing stop, 0.1% a trade, settled closes. All three start
from the 13 Nov 2026 decision, the one the live book bought at the 16 Nov open, so
they differ only in how often they rebalance. Returns are on the whole sleeve,
cash included.

The 2008-to-now study (MOMENTUM_ROTATION_GATES_V2_REBALANCE_EXITS) found that
neither faster schedule beat monthly. This record asks whether live markets agree.

## What would change the live rule (fixed now)

1. **No decision before 26 weeks** (about six monthly decisions). Fewer cycles cannot
   separate the schedules from chance.
2. A faster schedule is a **candidate** only if, after 26 weeks or more:
   - its return since start beats monthly by **at least 2 percentage points**;
   - it is ahead of monthly at the end of **at least 2 of the last 3 calendar quarters**;
   - its worst drawdown is **no more than 2pp deeper** than monthly's.
3. A candidate is **not** adopted directly. It triggers a new pre-registered
   backtest that includes the forward period. The live rule changes only if that
   backtest passes the usual mark: both halves, Sharpe at least control + 0.05,
   CAGR at least control, and drawdown within 2pp.
4. If no schedule qualifies, monthly stays and this record continues.

## Known limits

- Fills are modelled at the open with 0.1% cost, not at real paper fills. The live
  book's own P&L, from IBKR fills, is reported separately.
- The weekly model trades about 4 times as often as monthly. If the 0.1% cost
  understates real costs, the faster schedules are flattered, not penalised.

## Addendum, 17 Nov 2026 — the weekly book also trades (pass mark unchanged)

Owner: "we need to capture many executions". The weekly schedule now also runs
as a real paper book, `momentum_rotation_weekly_t212`, on T212 demo — the same
engine with `--rebalance-every weekly`, sized on 90,000 (the demo holds ~48k
and the engine invests up to 50%). Its first decision is the 20 Nov close,
bought at the 23 Nov open; it does not buy the 13 Nov list late.

Why T212: IBKR reports one quantity per stock per account, so two rotation
books on one IBKR account cannot tell whose shares are whose. Known
differences from the IBKR monthly book, stated before any result: a different
broker's fills and fees; 90,000 not 150,000, so whole-share rounding is
coarser on expensive names; ECHO, VMRK and BRK-B are not on T212.

The decision rule above is NOT changed by this. The models (all three from the
same data, same costs) remain the comparison the pass mark is judged on. The
weekly book answers a second question the model cannot: what weekly turnover
really costs in fills.
