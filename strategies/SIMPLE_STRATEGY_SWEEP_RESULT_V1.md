# Simple strategy sweep on daily bars — result, v1

Run 28 Nov 2026 against `SIMPLE_STRATEGY_SWEEP_GATES_V1.md` (committed before
any return was computed). Nothing was tuned after the run.

## VERDICT: no strategy wins.
None of the six rules beat buy-and-hold on both tests (max drawdown ≤ 60% of
the benchmark's AND a better return-to-drawdown ratio) in both periods.

Evaluation from 22 Sep 2022. In-sample to 31 Oct 2024; out-of-sample
1 Nov 2024 – 27 Nov 2026. Annual return / max drawdown / Sharpe / time
invested.

### ETFs (SPY, QQQ, SMH, IGV, equal weight) — the cleaner evidence
| Strategy | In-sample | Out-of-sample |
|---|---|---|
| Buy and hold | 33.4% / −14.0% / 1.45 / 100% | 17.3% / −24.3% / 0.75 / 100% |
| S1 trend-200 | 24.6% / −13.5% / 1.36 / 83% | 13.5% / −15.0% / 0.81 / 81% |
| S2 Donchian 55/20 | 10.7% / −7.7% / 0.89 / 51% | 7.3% / −15.3% / 0.66 / 46% |
| S3 RSI-2 pullback | 13.4% / −6.9% / 1.30 / 27% | 4.3% / −10.2% / 0.40 / 30% |
| S6 ETF rotation (top 1) | 37.7% / −24.8% / 1.34 / 83% | 23.7% / −38.1% / 0.79 / 96% |

### Stocks (25 names the owner traded in 2026 — hindsight universe)
| Strategy | In-sample | Out-of-sample |
|---|---|---|
| Buy and hold, equal weight | 44.0% / −16.5% / 1.61 / 100% | 52.4% / −30.1% / 1.52 / 100% |
| S1 trend-200 | 22.4% / −12.8% / 1.41 / 63% | 32.5% / −17.8% / 1.54 / 67% |
| S2 Donchian 55/20 | 9.7% / −8.3% / 0.93 / 39% | 23.3% / −9.2% / 1.65 / 41% |
| S3 RSI-2 pullback | 7.0% / −6.4% / 0.89 / 24% | 4.5% / −11.7% / 0.41 / 27% |
| S4 momentum top 5 | 58.8% / −29.1% / 1.45 / 100% | 97.3% / −49.4% / 1.41 / 100% |
| S5 momentum + QQQ filter | 41.5% / −29.1% / 1.20 / 83% | 64.7% / −48.7% / 1.21 / 86% |

## What the numbers do say
1. **Trend exits cut drawdowns, and cost return.** On the stocks,
   out-of-sample, the 200-day rule cut the worst drawdown from −30% to −18%
   and the 55/20 breakout rule to −9%, with Sharpe at or above buy-and-hold.
   In the 2023–24 rally the same rules gave up a half to three-quarters of
   the return. That is a trade, not a free win — and it is the trade the
   operating model's stop rule (section B) makes on purpose.
2. **Concentrated momentum is the highest return and the deepest
   drawdown.** Top-5 momentum made the most in both periods and fell 49%
   peak to trough out-of-sample; the QQQ filter did not help (−48.7%). Any
   momentum sleeve needs a size limit set by that drawdown, not by its
   average return. Relevant to the momentum rotation forward test.
3. **Short-term mean reversion (RSI-2) did not hold up** out-of-sample on
   either universe.
4. **The stocks were not the problem.** An equal-weight, unlevered basket of
   these same 25 names returned about 52% a year over the last two years with
   a −30% drawdown. The live account lost about 40% of deposits over part of
   that window. The difference is entry timing (most capital went in between
   July and November 2026), concentration in one sector, leverage and naked
   puts — not stock selection or the absence of a signal.

## Limits
- **Hindsight universe.** The stock list was chosen in 2026; every stock
  result, especially S4/S5 and the buy-and-hold figure, is flattered by it.
- Four years of data, one bull phase and one sharp fall; two periods are a
  thin test. Seven rules on one history: an isolated good cell (e.g. Donchian
  on stocks out-of-sample) is as likely luck as edge.
- Prices unadjusted for dividends; 0.05% cost per side, no slippage; equal
  weight rebalanced daily; cash earns nothing.
- RSI(2) uses a two-change Cutler form rather than Wilder smoothing.
- No options strategy is tested here — that needs option price history.

## Prediction vs outcome
Predicted "none wins on both periods": correct. Predicted S1 and S5 would cut
drawdown: S1 did; S5 did not. Predicted S4 would look best in-sample: it did,
and also out-of-sample on return, with the worst drawdown.
