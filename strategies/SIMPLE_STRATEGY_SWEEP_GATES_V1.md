# Simple strategy sweep on daily bars — gates, v1

Status: PRE-REGISTERED 28 Nov 2026, committed before any strategy return was
computed. Owner asked for a general evaluation: "see if we can get any
winning strategy."

## Data
IBKR daily bars (regular hours, unadjusted for dividends), Nov 2021 – 27 Nov
2026. ETFs: SPY, QQQ, SMH, IGV. Stocks (25): AAPL ABBV ACN AMD AMZN APLD CRDO
DELL GOOGL HOOD IBM INTC MDB META MRVL NVDA PLTR QCOM SNOW STX TSLA UNH VLO
VZ WDC XOM.

**Known bias, stated up front:** the stock list is the names the owner
traded in 2026, chosen with hindsight. Results on the stock universe flatter
anything that buys strength. The ETF results are the cleaner evidence.

## Strategies (fixed; no parameter search)
Signals at the close, positions from the next session, 0.05% cost per side,
cash earns nothing, long only, equal weight across the universe unless
stated.
- **B&H** buy and hold (benchmark, same universe).
- **S1 trend-200:** hold when close > 200-day average, else cash.
- **S2 Donchian:** enter on a 55-day closing high, exit on a 20-day closing low.
- **S3 RSI-2 pullback:** close > 200-day and RSI(2) < 10 → buy; exit when
  close > 5-day average.
- **S4 stock momentum:** every 21 sessions hold the top 5 stocks by
  126-session return skipping the latest 21.
- **S5 momentum with market filter:** S4, but in cash whenever QQQ ≤ its
  200-day average.
- **S6 ETF rotation:** every 21 sessions hold the one ETF of the four with
  the best 126-session return, if positive; else cash.

## Periods
Evaluation starts once 200 sessions of history exist (about Oct 2022).
In-sample: start – 31 Oct 2024. Out-of-sample: 1 Nov 2024 – 27 Nov 2026.

## What counts as winning
A strategy WINS only if, **in both periods**, against buy-and-hold of the
same universe: (a) its max drawdown is at most 60% of the benchmark's, and
(b) its return ÷ max drawdown is higher. Reported for each: annual return,
max drawdown, Sharpe, time invested, trades. No tuning after the run; seven
rules on one history means one "winner" by luck is plausible, so a win is a
candidate for forward testing, not a finding.

## Prediction
None wins on both periods. S1 and S5 cut drawdown but lag in the 2023–24
rally; S3 has too little exposure to matter; S4 looks best in-sample because
of the hindsight universe.
