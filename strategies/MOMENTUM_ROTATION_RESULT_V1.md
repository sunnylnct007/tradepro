# Momentum rotation: backtest result v1 (12 Nov 2026)

Gates and rules: MOMENTUM_ROTATION_GATES_V1.md, committed before this run.
Reproduce: `uv run tradepro-momentum-backtest --robustness`.

**Read every number below as an upper bound.** The universe is today's 99 most
liquid S&P 500 names (9 per sector). Every one of them survived and grew into a
leader by 2026; names that failed between 2008 and 2026 are absent.

## Data

Golden store after the 12 Nov re-convention: one price convention
(`close * adj_factor`, dividend-adjusted), 41,584 missing sessions filled, no
gap longer than five days, SPY complete through 2008. 2008-01-02 to 2026-11-12.

## Main run: the decided design (account, engine at most 50% of NLV)

| | CAGR | Worst daily drawdown | Sharpe | Worst 12 months |
|---|---|---|---|---|
| Account (decided design) | 8.4% | -13.0% | 0.98 | -6.4% |
| SPY, fully invested | 11.2% | -51.9% | 0.64 | -47.4% |
| SPY at the same 50% budget | 7.9% | -25.9% | 0.69 | -22.7% |
| Equal-weight universe, fully invested | 18.1% | -45.9% | 0.92 | -36.5% |
| Equal-weight universe at 50% | 9.2% | -25.3% | 0.92 | -18.2% |

Activity: 1,397 trades, turnover 3.0x a year, average holding 77 days, 158
trailing-stop exits, fully in cash 22% of days, average invested 33.5% of NLV,
largest sector weight 22.7%.

The engine alone, without the account wrapper (allocation 100%, alerts off):

| | CAGR | Worst daily drawdown | Sharpe | Average invested |
|---|---|---|---|---|
| With the 20% vol target | 15.8% | -24.6% | 0.97 | 64% |
| Without the vol target | 21.8% | -26.7% | 1.03 | 75% |

## Robustness: 22 variants run, every one reported

15 of 22 beat SPY at the same 50% budget (7.9%).

| Variant | CAGR | Drawdown | Sharpe |
|---|---|---|---|
| decided design | 8.44 | -13.0 | 0.98 |
| lookback 3 months only | 7.49 | -12.5 | 0.91 |
| lookback 6 months only | 8.47 | -11.2 | 0.99 |
| lookback 9 months only | 8.58 | -13.3 | 0.98 |
| lookback 12 months only | 6.29 | -15.7 | 0.75 |
| 12-minus-1 | 6.09 | -14.7 | 0.74 |
| 8 names | 8.79 | -13.7 | 1.01 |
| 12 names | 8.20 | -12.2 | 0.97 |
| 15 names | 8.06 | -11.6 | 0.97 |
| month-end rebalance | 6.98 | -17.1 | 0.83 |
| rebalance 5 days earlier | 8.08 | -13.9 | 0.94 |
| rebalance 5 days later | 7.66 | -14.4 | 0.88 |
| market filter: SPY 10-month | 8.44 | -13.0 | 0.98 |
| market filter: breadth | 8.50 | -13.0 | 0.97 |
| market filter: none | 9.54 | -16.6 | 1.00 |
| vol target off | 11.42 | -14.5 | 1.01 |
| vol target 15% | 6.75 | -12.0 | 0.95 |
| vol target 25% | 9.52 | -13.0 | 0.99 |
| trailing stop off | 8.78 | -17.2 | 0.95 |
| trailing stop 15% | 7.48 | -10.1 | 0.94 |
| trailing stop 25% | 8.66 | -14.8 | 0.98 |
| sector cap off | 8.45 | -13.0 | 0.98 |

Not yet run: four weekly tranches; walk-forward; the point-in-time universe.

## Gates

| # | Test | Result |
|---|---|---|
| G1 | Beats SPY after costs | Account 8.4% beats SPY at the same budget (7.9%) but not SPY fully invested (11.2%). The engine alone (15.8%) beats SPY. Passes on a like-for-like budget. |
| G2 | Beats equal-weight hold of the same universe | **FAILS.** Account 8.4% vs 9.2% at the same budget; engine alone 15.8% vs 18.1%. Only the no-vol-target engine (21.8%) beats it. |
| G3 | Drawdown x 50% inside 25% | Passes: engine alone -24.6% x 50% = 12.3%. |
| G4 | Walk-forward keeps half the in-sample return | Not run yet. |
| G5 | Most variants beat SPY | Passes: 15 of 22 at the same budget. |

## What the numbers say

1. The design works as a risk reducer: it roughly halves SPY's drawdown at a
   similar or better return for the capital at risk.
2. **The 20% volatility target costs about six points a year and saves about two
   points of drawdown** (engine alone: 15.8% / -24.6% with it, 21.8% / -26.7%
   without). The spec required it to lower drawdown "without giving up most of
   the return"; on this evidence it does not.
3. Holding the same 99 names equally beats the decided design. That comparison
   flatters the hold more than anything: survivors chosen in 2026 are exactly
   what an equal-weight hold rewards. The point-in-time test is the one that can
   separate momentum from survivorship.
4. The blend is not fragile: 6- and 9-month lookbacks match it, 12-month and
   12-minus-1 are weaker. The market filter choice barely matters on this
   universe; month-end rebalancing is worse than mid-month.
