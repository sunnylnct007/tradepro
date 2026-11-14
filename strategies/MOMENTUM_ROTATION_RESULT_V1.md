# Momentum rotation: backtest result v1 (12 Nov 2026)

Gates and rules: MOMENTUM_ROTATION_GATES_V1.md, committed before this run.
Reproduce: `uv run tradepro-momentum-backtest --robustness`.

**Decided design since 12 Nov (after these results): volatility target 25%,**
not 20%. Its row is "vol target 25%" below: 9.5% CAGR, -13.0% drawdown, Sharpe
0.99. The tables keep the original 20% design as the baseline they were run on.

**Read every number below as an upper bound.** The universe is today's 99 most
liquid S&P 500 names (9 per sector). Every one of them survived and grew into a
leader by 2026; names that failed between 2008 and 2026 are absent.

## v1.1 — the build pack universe (13 Nov 2026, late)

Rerun on the build pack's rule 1 universe: 142 harvest symbols with a sector
label (the core ~100 plus live-book, sector picks and names toggled on in the
universes page), all rewritten into the one price convention first (STX, UAL
fixed on the way). Volatility target 25% (owner).

| | CAGR | Worst daily drawdown | Sharpe |
|---|---|---|---|
| Account (engine at most 50%) | 9.7% | -14.4% | 0.98 |
| SPY at the same 50% budget | 7.9% | -25.9% | 0.69 |
| SPY, fully invested | 11.2% | -51.9% | 0.63 |
| Engine alone at 100%, alerts off | 19.1% | -25.5% | 1.02 |
| Equal-weight universe, fully invested | 18.8% | -47.0% | 0.95 |

The engine alone now edges the equal-weight hold (19.1% vs 18.8%) with about
half its drawdown; on a survivor universe that is close to a draw on return.

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

Not yet run: four weekly tranches; the point-in-time universe.

## Gates

| # | Test | Result |
|---|---|---|
| G1 | Beats SPY after costs | Account 8.4% beats SPY at the same budget (7.9%) but not SPY fully invested (11.2%). The engine alone (15.8%) beats SPY. Passes on a like-for-like budget. |
| G2 | Beats equal-weight hold of the same universe | **FAILS.** Account 8.4% vs 9.2% at the same budget; engine alone 15.8% vs 18.1%. Only the no-vol-target engine (21.8%) beats it. |
| G3 | Drawdown x 50% inside 25% | Passes: engine alone -24.6% x 50% = 12.3%. |
| G4 | Walk-forward keeps half the in-sample return | Passes: the pick (6-month lookback) made 7.3% in 2008-2017 and 9.5% in 2018-now. |
| G5 | Most variants beat SPY | Passes: 15 of 22 at the same budget. |

## Walk-forward (method fixed in the gates file before the run)

| | 2008-2017 (in-sample) | 2018-now (out-of-sample) |
|---|---|---|
| Selected by in-sample Sharpe: 6-month lookback | 7.3% CAGR, -11.0% DD, Sharpe 0.95 | 9.5%, -11.2%, 1.01 |
| Decided design, unselected (control) | 6.4%, -11.4%, 0.84 | 10.5%, -13.0%, 1.10 |

Rolling yearly re-selection, 2018-2026, chained: **11.0% a year.** It chose the
6-month lookback every year to 2024, then "vol target off" in 2025 and 2026.

Both runs did better out of sample than in. Two caveats: 2018-2026 was a strong
period for exactly these survivors, and each rolling year starts in cash until
its first mid-month rebalance.

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


## Review of the first list (14 Nov 2026)

An outside review of the 13 Nov decision (MRNA, MU, MPC, VLO, DELL, PSX, AMD,
TGT, MTD, MRK) raised trend health, outlier risk, concentration and earnings.

- Confirmed: six picks closed below their 50-day average; three are refiners.
- Corrected: its volatility figures were 3-5x too high (standard deviation of
  price LEVELS, not daily returns). Returns-based 60-day vol: MRNA 95%, MTD 25%.
  At ~2.6% a name with a 20% trailing stop, any one name risks about 0.5% of
  the account.
- Earnings: the rules allow holding stocks across earnings (the options rule
  does not apply to stock); the trailing stop covers a gap.

Its four proposed rules, 142-name universe, 2008-now:

| Change | Account CAGR | Drawdown | Sharpe | Engine alone CAGR / DD |
|---|---|---|---|---|
| decided design | 9.68% | -14.4% | 0.98 | 19.1% / -25.5% |
| veto picks below the 50-day | 10.29% | -14.5% | 1.04 | 20.0% / -26.3% |
| rank by return / volatility | 8.96% | -13.9% | 1.00 | 17.2% / -25.9% |
| cap scores at 3x the median | 9.35% | -14.4% | 0.95 | 18.2% / -25.5% |
| two names per sector | 9.38% | -13.0% | 0.97 | 18.6% / -24.4% |
| all four | 9.18% | -12.3% | 1.06 | 18.0% / -23.0% |

Outcome: trade 16 Nov as decided; the 50-day filter from the 15 Dec signal, as
a recorded change. Caveat logged: a survivor universe likely understates that
filter's value, since the hardest fallers are missing from the sample.
