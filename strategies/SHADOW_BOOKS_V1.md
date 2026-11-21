# SHADOW BOOKS V1 — several strategies recorded in parallel, no orders

Pre-registered 21 Nov 2026, BEFORE any forward result below was computed.
Owner, 21 Nov: "if we keep on evaluating strategy like this it will take too
long. can we not evaluate a bit more strategy in parallel" → "yes go with it";
"this is where we need multiple strategy and evaluation".

A shadow book is a frozen rule replayed every night on SETTLED bars from a
fixed start date. It never sends an order (the module has no broker import).
The record is recomputed from the start date on every run, like the intraday
record, so a missed night can never leave a gap or a double count.

## The books

| key | rule (imported, never retyped) | record starts | why that date |
|---|---|---|---|
| `swing` | `signals/mean_reversion` — close < 20d mean − 2.25σ while above the 200-SMA; exit at the 20d mean, −8% stop, 20 sessions | 2026-09-09 | the −2.25σ entry was frozen 8 Sep (1a9088f4); every later session is data the rule never saw |
| `pullback` | `signals/momentum_pullback` — the screen's own `_entry_signal`; −8% hard stop, 8% trail from the peak, 60 sessions | 2026-09-01 | MOMENTUM_GATES_V2 frozen 22 Aug (ca494bf2); September onwards is unseen |
| `rotation_defensive` | the rotation engine with `market_exit="spy50_daily"` (V4) beside the control | 2026-11-13 | same decision the live book bought; V4 graded it on 2008–2026 so only the future is unseen |
| intraday SPY<200d tag | rules 2 and 7 of the intraday record, split by SPY's prior close vs its 200-day | 2026-11-16 | TECHNICAL_SHORTS_V1 lead (1): accumulate n going forward |

Daily books (swing, pullback):

* Universe: the harvested universe (`universe_symbols()`), adjusted closes
  (`close × adj_factor`). A series that `poison_check` refuses is excluded and
  NAMED; a trade spanning a one-day move over 35% (the studies' corrupt-bar
  guard) is dropped and NAMED, never silently counted.
* Signal on a settled close → buy at the NEXT open. Exit checked on the close
  (the signal module's `exit_decision`) → sell at the NEXT open. This is what a
  live book can actually do; it is not the studies' intraday-touch fill.
* Every signal is taken, one position per symbol at a time (the gates graded
  take-every-signal). $10,000 a trade, 0.10% round trip.
* A trade not yet exited is OPEN: marked at the latest close, shown, and kept
  OUT of the record.
* Condition tags on each trade, at the signal date: SPY above/below its
  200-day, and SPY 20-day volatility calm / normal / stressed
  (EVALUATION_CONDITIONS_V1 definitions).

## Pass marks — fixed here, never moved

A verdict is printed only at or above the minimum n; below it the book reads
**"too few (n of N)"**, never a pass or a fail.

| book | min n | PASS | FAIL | backtest reference |
|---|---|---|---|---|
| swing | 50 closed trades | mean ≥ +0.49%/trade (half of +0.97%) AND win ≥ 60% | mean ≤ 0 | +0.97%/trade next-open, ~72% win |
| pullback | 50 closed trades | mean ≥ +0.77%/trade (half of +1.53%) | mean ≤ 0 | +1.53%/trade, 47% win |
| rotation_defensive | 6 monthly decisions | worst drawdown since start smaller than the control's AND return no more than 3 points behind it | return more than 3 points behind with a drawdown no smaller | V4: 6.42% vs 9.51% CAGR, −11.77% vs −14.36% worst DD |
| intraday SPY<200d | 50 gated trades per rule | mean ≥ +0.15%/trade | mean ≤ 0 | +0.27% (rule 2) / +0.17% (rule 7), n≈29 |

Between PASS and FAIL the book reads **"below the mark — keep recording"**.

## What this does NOT claim

* The universe is the names harvested since 9 Nov — chosen after the swing
  and pullback start dates. Over 2–3 months that is a small survivor bias in
  the longs' favour; it is stated, not removed.
* Fills are the next open from daily bars, not executions. A book that passes
  here has shown the RULE still has an edge; slippage is a separate question,
  answered only by a placed order.
* No book changes a live rule. A pass earns a proposal to the owner, nothing
  automatic.

## Amendment — 21 Nov 2026, after the first run (conservative only)

The first run printed **pullback: FAIL** on 77 closed trades (−5.2%, 13% win)
while 61 trades were still open at +0.4% — no September entry had yet reached
the 60-session timeout, so every close so far was a stop or a trail. A record
of closed trades is losers-first until the first cohort could have timed out.

Rule added: a daily book prints **no verdict** ("deferred — record censored")
until at least `MAX_HOLD` sessions have passed since its record start (swing
20, pullback 60 → from about 26 Nov). This can only delay a verdict, never
upgrade one. The marks themselves are unchanged. Also added as display splits,
not marks: the closed record by `sector_stock` / `no_sector` (the repo's ETF
rule), and the open book's marked P&L beside the closed record.
