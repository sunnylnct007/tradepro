# Support/Resistance level study v1 — PRE-REGISTERED gates

**Committed BEFORE the first run** (15 Aug 2026). Same protocol as the wheel
backtest gates: thresholds fixed in advance, a prediction on record, and a
standing rule that a failure is an answer rather than a prompt to tune.

## The question

The desk draws support and resistance lines on every candle chart
(`CandleIchimokuChart.tsx::pivotLevels`). **Nobody has ever tested whether
price respects them.** The owner wants an intraday strategy that sells at
resistance and buys at support, so the signal's validity has to be established
before any strategy is built on it — otherwise the strategy's edge and the
signal's validity fail together and neither can be diagnosed.

> When price touches one of our levels, does it reject more often than an
> identically-placed line that is NOT a level?

## What is being tested — the SHIPPED algorithm, not a new one

The Python port must reproduce `pivotLevels` exactly (per the standing rule:
never hand-reimplement, port verbatim and pin with a parity test):

- bar *i* is a swing HIGH if `high[i]` is the maximum over `[i-win, i+win]`;
  a swing LOW if `low[i]` is the minimum. `win = 5`.
- near-equal pivots collapse into one level when within `clusterPct = 0.005`
  (0.5%), the level becoming the running mean and `touches` incrementing.
- only the most recent `maxScan = 240` bars are scanned.

Any deviation from the shipped constants invalidates the study — the point is
to grade **the lines actually on the screen**.

## Causality — the thing most easily got wrong

A swing high at bar *i* is not knowable until bar *i + win*. At every decision
bar *T* the study may use **only** pivots with `i + win <= T`, built from the
240 bars ending at *T*. Using the full series to find pivots and then testing
"reactions" to them is look-ahead, and it is the single most likely way to
manufacture a spurious edge here. Pinned by a test.

## Definitions, fixed in advance

- **Touch (resistance):** the bar's range contains the level (`low[T] <= L <=
  high[T]`) and the level sits above the prior close (`close[T-1] < L`) — i.e.
  price approached from below.
- **Touch (support):** range contains the level and `close[T-1] > L`.
- **Outcome horizon:** `N = 5` bars.
- **Rejection (resistance):** `close[T+N] < close[T]` — price turned away.
- **Rejection (support):** `close[T+N] > close[T]`.
- One touch per (symbol, level, day). A level touched on consecutive days
  counts each day; this is disclosed, not corrected.

## The control — the crux of the whole study

Prices mean-revert somewhat regardless, so a raw rejection rate near 50% proves
nothing. Two controls, both reported:

- **C1 (drift control, disclosure only):** the unconditional rate of the same
  outcome across all bars in the sample.
- **C2 (placebo levels, THE primary control):** for every real level, a
  synthetic line at the same distance from the prior close but at a price with
  no pivot within `clusterPct`. Touches on placebo lines are measured
  identically. **The edge that matters is real minus placebo.**

## Data

Daily bars, IBKR-sourced from the bar store, over the deepest clean history
available. **Declared limitation, before any numbers exist:** the owner's
target is an INTRADAY strategy, and this is a DAILY study. It is run on daily
bars because the intraday store is ~249/251 symbols yfinance-sourced with
roughly 7 days of 1-minute history — there is no intraday series to test on.

Daily is therefore a **filter, not a proof**. A daily failure does not strictly
prove intraday failure (microstructure effects are real and are strongest at
short horizons). But a signal with no effect on the timeframe where we DO have
years of clean data is a poor foundation for one where we have a week of
fallback data, and that is the honest reading either way.

## Gates

| # | Test | Pass |
|---|------|------|
| **V0** | Touch events per side (validity, not performance) | ≥ 2,000 |
| **G1** | Resistance rejection rate − placebo rate | ≥ +5.0 pts |
| **G2** | Support rejection rate − placebo rate | ≥ +5.0 pts |
| **G3** | Multi-touch (≥2) levels reject at ≥ the rate of single-touch levels | true |
| **G4** | The G1/G2 edges have the SAME SIGN in both halves of the period | true |

**Why +5.0 points.** A mean-reversion strategy pays spread and commission on
every entry and exit, and trades often. A coin-flip signal needs several points
of edge before costs to survive them. 5 points is the minimum that could
plausibly clear a real cost model — set now, before any number is visible.

**V0 is a validity gate.** Below 2,000 events the study reports UNDERPOWERED
and answers nothing; that is neither a pass nor a licence to proceed.

**G3 is the falsification test and the most informative gate here.** The chart
labels levels `R×3` / `S×2` on the theory that a price turned at repeatedly is
stronger structure. If that is real, rejection must increase with touch count.
If a "stronger" level is not stronger, the construct is noise dressed as
structure, and G1/G2 passing would deserve suspicion rather than trust.

## Prediction — recorded before the work

**I predict G1 and G2 FAIL**, with a real-minus-placebo edge in the 0–3 point
range rather than ≥5. Reasoning stated in advance so it can be judged:

1. Pivot-based S/R is among the most widely known techniques in retail
   technical analysis; a large, simply-computable edge is the least likely
   outcome on liquid US equities.
2. The construction is naive by design: no volume at the level, no recency
   weighting (a pivot 240 bars old counts as much as one from last week), and
   fixed `win`/`clusterPct` regardless of the name's volatility.
3. The placebo control is strict. Much of the apparent "respect" for levels is
   ordinary mean reversion, which the placebo will also capture.

**G3 I genuinely do not know**, and it is the result I most want to see: it
tests the construct rather than the trade.

If the study fails, the answer is that the intraday S/R strategy does not have
a validated signal underneath it — not that the study needs different
parameters. **No sweeping `win`, `clusterPct`, `N` or the touch definition
after seeing results.** A v2 would need a new gates file and a stated reason
for the change that is not "v1 failed".

## What a pass would and would not license

A pass licenses **porting the study to intraday data once that data is
IBKR-sourced** — nothing more. It would not license trading, and specifically
not the short leg, which conflicts with the standing spec in `docs/*.py`
(long/flat, trade-the-delta, NEVER short). That conflict is the owner's to
resolve consciously, not something a hit-rate study settles.
