# DATA_CHANGE_LOG — every change to stored market data

**Why this exists.** A forward test is only interpretable if you can tell a
strategy's behaviour apart from a change in the data underneath it. During a
live test window, a disappointing week and a silent data correction look
identical after the fact.

**A blanket freeze is not credible** and should not be promised: at the current
rate of discovery, correctness bugs are still surfacing weekly, and knowingly
serving data you know to be wrong is worse than changing it. So the rule is not
"nothing changes" — it is **nothing changes silently**.

## The rule

During any live forward-test window:

1. **No discretionary changes.** No convention changes, no universe edits, no
   refactors of stored data, no re-sourcing sweeps — unless correctness demands it.
2. **Corrections are allowed and must be logged here** before or immediately
   after they land, with: date, what changed, symbols affected, date range
   affected, and the commit.
3. **Routine harvesting is not a change** — that is the test running.

Any anomaly in a forward test can then be checked against this file first.

## Entries

### 2026-08-23 — IBKR volume was stored in 100-share lots
- **What**: every IBKR-sourced bar carried 1/100th of real volume. Migrated x100.
- **Symbols**: all with `source` starting `ibkr` — 1,513,859 rows / 10,092 partitions
  in the parquet store; `ibkr_price_bars` via db migration 065.
- **Range**: entire history.
- **Prices unaffected** — volume only. But volume feeds the liquidity floor, so it
  silently shaped which instruments the platform would trade (universe 89 → 244).
- **Commit**: 4064d5c. Both migrations idempotent (manifest marker / schema_data_migrations).
- **When it actually applied to Postgres: 2026-08-23 13:25:47 UTC**, NOT at 4064d5c.
  The parquet store changed at commit time, but db migration 065 failed on every
  startup attempt for six hours first — it rewrites 1.6M rows and Dapper's default
  30s command timeout cut it off, so the transaction rolled back and the API refused
  to start. `ibkr_price_bars` volumes therefore changed under a running system at
  13:25:47, hours after the parquet store did. If anything in a forward test
  straddles 23 Aug, the two stores disagreed on volume units in between.
  Fixed in b7f2183 (900s migration timeout). SPY 2026-08-21 daily: 589,831 → 58,983,100.

### 2026-08-22 — wrong-contract (foreign listing) data purged
- **What**: partitions holding a different listing's series, in LSE pence.
- **Symbols**: MTUM, QUAL, USMV, VLUE, STX (daily + some 5m); STX 1m.
- **Range**: scattered 2022-2026; 18 daily partitions, 5 intraday, 1 1m.
- **Commits**: fee4867, a9cddc8.

### 2026-08-22 — 52-week high/low convention changed to INTRADAY
- **What**: `market_state` used the highest CLOSE; now uses the intraday extreme,
  matching IBKR, stockanalysis.com and the chart's S/R overlay.
- **Affects**: `pct_off_52w_high`, range position, for every symbol.
- **Commit**: 5fd0b07.

### 2026-08-22 — dividend yield was 100x on low-yield names
- **What**: two yfinance fields with different units run through one heuristic.
- **Symbols**: any with a true yield below ~1% (MU, AAPL, WCC…).
- **Commit**: 5fd0b07 (+ research lane's b4a4fdd for the second site).

### 2026-08-22 — universe reorganisation
- **What**: `us_equity` tree retired, LSE ETFs moved to `uk_equity`, futures /
  crypto / foreign quarantined. Bar-cache directory listing went 286 → 250.
- **Commits**: 8acdc49, 31975c1.
