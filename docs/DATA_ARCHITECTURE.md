# TradePro data architecture — bars

**Status:** ruling, 11 Nov 2026. Supersedes every earlier description of where
price bars live (listed at the end). Enforced by
`strategies/tests/test_one_golden_bar_store.py`: the suite fails when a bar
reader or writer appears outside the allow-list in that test.

## The rule

> One golden bar store. One writer. One read path.

| Role | The one | Nothing else |
|---|---|---|
| Store | Parquet partitions under `bar_cache/<asset_class>/<SYMBOL>/<resolution>/<YYYY-MM>.parquet`, mirrored to `s3://<bar-cache-bucket>/bar_cache/` | No Postgres bar table, no `~/.tradepro/cache`, no per-process bar files |
| Writer | `tradepro_strategies.bar_cache.store.BarStore`, driven by the harvest lanes (`tradepro-bar-cache-harvest`) | No API process writes bars. No strategy, screen, MCP tool or Lambda writes bars except through `BarStore.get` on a documented cache miss |
| Read path | `BarStore.get` in Python; `GET /api/marketdata/candles` for every screen and external caller, backed by the same store | No endpoint reads bars from anywhere else. No code globs the parquet directory directly |
| Freshness | Every bar grade is shown beside the **newest bar's timestamp**. "Complete" without a timestamp is not a grade | No self-grading lane without that number |
| Provenance | Each row carries `source` (`ibkr_web`, `ibkr`, `yfinance`, …) and `adj_factor`. A fallback row is labelled, never silent; the next harvest retries the golden provider for that window | No mixed-convention rows without a label |

Why this is a ruling and not a preference: every data incident since July had
the same shape. Two stores filled on different schedules (the chart missed
DOCN's +12.6% day because it read the copy that fills before the close); a
fix applied at one of two write sites (the daily clamp of 1 Sep that the
intraday delta path never got, so the 5m lane wrote no bar for the open
session for ten weeks while grading itself complete); fallback rows written
during a ten-minute IBKR outage sitting beside raw rows. Each was fixed where
it was found, and the knowledge lived in session notes, not in the code.
This file and its test are the fix for that.

## What exists today (inventory, 11 Nov 2026)

Everything below is a bar store, writer or reader found in the repo. Items
marked **KEEP** are the design. Everything else is **RETIRE**, in the order
of the migration plan, and may not gain new callers from now on.

### Stores

| Store | Verdict | Notes |
|---|---|---|
| Parquet `bar_cache` + S3 mirror (`strategies/tradepro_strategies/bar_cache/`) | **KEEP** — golden | 1m, 5m, 1d partitions; manifests; write-through and read-through to S3 |
| Postgres `ibkr_price_bars` (migration 057) | RETIRE | 1m + 1d, filled during the day by the API; the copy the charts read |
| `~/.tradepro/cache/<provider>/1d` (`strategies/tradepro_strategies/cache.py`) | RETIRE | Legacy daily cache, still written by ~10 callers through `ibkr_bars` fallback |
| `~/.tradepro/cache/intraday` (`paper/sources/cache.py`) | RETIRE | Paper-trading yfinance intraday cache, fallback after `BarStoreSource` |
| Quarantine / backup parquet trees under `~/.tradepro` | Not read paths | Leave; housekeeping only |

### Writers

| Writer | Verdict |
|---|---|
| Harvest lanes → `BarStore.get` (`bar-cache-harvest-daily`, `bar-cache-harvest-5m`, `bar-cache-resource-intraday`, `bar-cache-audit`, `bar-cache-mirror`) | **KEEP** — the single writer |
| `BarStore.get` write-on-miss from strategies, MCP `bar_cache_get_bars`, Lambda jobs | KEEP, through the store only |
| `IBKRBarHarvester` (API hosted service, Postgres) | RETIRE |
| `IBKRDailyBackfillService` (API hosted service, Postgres) | RETIRE |
| `POST /api/admin/data-trust/bars` ← `tradepro-bar-cache-push`, `tradepro-bar-reconcile --repair` | RETIRE |
| `cache.py` `refresh_symbol` / `ensure_cached` | RETIRE |
| `paper/sources/cache.py` `CachedSource` | RETIRE |
| `data_ops` handlers + `tradepro-data-worker` (plist disabled 23 Sep; `S3BarCacheStorage` never built) | RETIRE — delete |

### Read paths

| Reader | Verdict |
|---|---|
| `BarStore.get`, `ibkr_bars.golden_daily` / `fetch_daily_bars` | **KEEP** |
| `GET /api/marketdata/candles` | **KEEP** — becomes store-backed (today: live Yahoo + a Postgres tail) |
| `GET /api/integrations/ibkr/bars`, `/bar-coverage` (Postgres) | RETIRE |
| `GET /api/integrations/ibkr/price-history` (live IBKR, no store) | RETIRE as a chart source; may stay as a raw diagnostic |
| `daily_bars.load_daily` and every direct `read_parquet`/glob of `bar_cache` (~30 sites: candidates, today_setups, signal_audit, fill_replay, studies, scripts) | RETIRE — route through `BarStore.get` |
| Direct `yfinance` bar fetches with no store (`data.py`, `bar_bus`, `index_strangle_*`, `preearnings_watch`, `live_quote`, `chains.py`) | RETIRE — route through the store or label as a quote, not a bar |
| Frontend `ibkrBars` (intraday chart) | RETIRE — `candles` with an interval |
| MCP `get_ibkr_bars`, `get_ibkr_price_history`, `get_ibkr_harvest_coverage`, `ibkr_fetch_bars` | RETIRE |
| `tradepro-analysis-server` sidecar + `/api/symbol-analysis` proxy (no consumer; the card moved to Lambda) | RETIRE — delete |

## Migration order

Each step is its own PR with its own verification. Nothing is wrapped; retired
code is deleted when its last caller goes.

1. **This PR.** The ruling, the inventory, the structural test, dead docs removed.
2. **API reads the golden store.** DONE 12 Nov 2026 (#381, #383; ccit-infra #2 grants the EC2 role read on `bar_cache/*`). `bar_cache` provider (`Parquet.Net` + S3 via the instance role, local directory for tests) is the default `IMarketDataProvider`; `/marketdata/candles` serves 1m/5m/1d from it with `sources`, `newestBar` and `location` on the series. The Postgres tail merge is skipped for the store and deleted in step 4.
3. **Frontend single call.** DONE 12 Nov 2026 (#386). The intraday chart asks `/marketdata/candles` with its interval; the `ibkrBars` client call and the `get_ibkr_bars` MCP tool are deleted. The Harvest view's coverage table still reads the Postgres summary until step 4.
4. **Postgres writers off.** DONE 12 Nov 2026 (#389), together with the data-ops queue. Deleted: the API `IBKRBarHarvester` (169 symbols × 1m every 300 s, also the largest consumer of IBKR's history pacing budget) and `IBKRDailyBackfillService`; the `data-trust/bars` push, `tradepro-bar-cache-push`, `tradepro-bar-reconcile` and the daily script's push/reconcile steps; `/integrations/ibkr/bars`, `/bar-coverage`, `/harvester-status`, `/daily-backfill-status`; the Postgres daily-tail merge in `/marketdata/candles`; the `tradepro-data-worker` lane, `data_ops` package, `/ops/run-data-*`, `/ops/poll-data`, `/ops/complete-data`, `data-trust/ibkr/fetch-bars`, `data-trust/ibkr/status`, `data-trust/backfill`; the Settings and Harvest panels and MCP tools that drove them. Replaced, not just removed: the strangle P&L feed alarm is a live IBKR snapshot (`IbkrLiveFeed`, shared with the quote endpoint); the options desk reads `GET /integrations/ibkr/pause`; desk-check's harvester watch is a golden-store freshness check. **Still to do:** drop `ibkr_price_bars` by migration after a parity week (no writer remains, so the table only ages).
5. **Python readers through the store.** `daily_bars.load_daily` becomes a thin wrapper over `BarStore.get`, then each direct parquet reader is moved and the wrapper deleted.
6. **Legacy caches deleted.** `cache.py` and `paper/sources/cache.py` and their directories. Blocked on one decision: raw OHLC + `adj_factor` (the store convention) versus adjusted close (the legacy convention) for the handful of callers that compute long-horizon returns. Owner decision; everything before this step does not depend on it.
7. **Freshness everywhere.** Newest-bar timestamp on the Data screen, the harvest summary line and the desk-check, with a stale rule by time of day.

## Superseded documents

Removed in step 1 (their content is either in this file or was never built):

- `docs/DATA_CONTRACT.md` — a consumption contract with an external data framework that was never built.
- `docs/data-sourcing.md` — May 2026 provider plan, Yahoo-centric, pre-dates the store.
- `PARQUET_HISTORY_GAP.md` — the two-store symptom (scanner on Postgres, backtests on parquet); resolved by this design.
- `docs/ARCHITECTURE.md` §10 "Parquet + DuckDB cache on the Mac" — describes the legacy `~/.tradepro/cache`; that section now points here.

Still valid and kept: `ADJ_FACTOR_MIGRATION_PLAN.md` (the store's price convention),
`DATA_CHANGE_LOG.md` (log of changes to stored bars).
