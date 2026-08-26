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

### 2026-08-26 — REPAIRED: the 25 Aug session was a PARTIAL bar on every symbol
- **What was wrong**: every 2026-08-25 daily bar in the store was the day SO FAR,
  not the settled session. Closes off by up to 0.94% (NVDA 211.05 stored vs
  213.05 true), volume 29–55% of actual.
- **Mechanism**: `_dedupe_sessions` keeps one row per session, preferring a
  golden source then the ALREADY-CACHED row on a tie. Both rows were
  `ibkr_web`, so the partial bar written during the session beat the settled
  bar the harvest fetched at 20:44. Introduced by the session-keyed merge that
  landed the same day, which is why the damage is exactly one session.
- **Scope, measured before repairing**: 30/30 symbols wrong on 2026-08-25;
  **0/30 wrong on every prior session** (14, 17, 18, 19, 20, 21, 24 Aug all
  clean). One day, not weeks.
- **Who read it**: Swing's screen ran 08:18 on those closes. The wheel screen
  reads the same bars via `fetch_daily_bars_with_provenance`. So did
  market_state and the digest.
- **Repair**: `--force-refresh` over 2026-08-22 → 2026-08-25, all 244 universe
  symbols. 244 complete, 244 GOLD, 0 failures. **Verified against the API
  afterwards rather than trusting the run's own summary**: 10/10 sampled
  symbols now match to 0.000% on close and 100% on volume.
- **Prevention**: volume now breaks the tie before arrival order (95460cc) — for
  one session the more complete bar has more volume, since a partial day cannot
  have traded more than the full day it belongs to. Provenance still outranks
  volume; prefer-existing still applies on a volume tie.
- **Note for anyone reading a harvest summary**: last night's run reported
  "244 complete, 243 GOLD, 0 partial" on bars that were ALL partial. "Complete"
  means every requested session returned a row, not that the row is finished.

### 2026-08-25 — SCHEDULED: tonight's harvest creates a volume UNITS BOUNDARY
Recorded BEFORE it happens, which is the point of this file.

- **What will change**: the ibkr_web provider no longer re-applies the lot→shares
  ×100 (6c22ebd). So from **tonight's 20:30 UTC harvest onward**, newly written
  ibkr_web volume is CORRECT, sitting beside historical rows that are 100× too
  high. Prices are unaffected — volume only.
- **Why it is not being avoided**: the alternative is knowingly continuing to
  write wrong data to keep it uniformly wrong. The repair of the historical rows
  is queued post-window because it moves the universe; the *provider* fix is not,
  because every night it is deferred writes another day of bad bars.
- **What is guarded already**, built yesterday in anticipation of exactly this:
  - `volume_vs_20d` withheld across a units change, both screens (research lane, 526ee32)
  - chart RVOL withheld, OBV truncated at the break (1a73cdb)
  - QuoteView absolute volume withheld; PriceHistoryChart strip annotated (6b84069)
  - shared detector `frontend/src/lib/volumeUnits.ts`
  - VWAP deliberately NOT guarded — volume cancels within a session, so a uniform
    error has no effect and a boundary between sessions cannot reach it
- **What is NOT guarded**: anything reading raw stored volume that we have not
  found. The 5m lane is inflated on the same basis and will cross the same
  boundary. If a volume-derived number looks wrong from 26 Aug, check whether its
  window spans tonight before assuming the market did something.
- **The forward test is unaffected**: Swing reads `close`; the universe is frozen
  at 244 and is not rebuilt during the window.

### 2026-08-25 — the daily harvest lane failed twice, and the second is UNEXPLAINED
- **24 Aug 20:30**: `ModuleNotFoundError` — a `uv run` above its `cd`, launchd's
  cwd. Research lane's, fixed same morning.
- **25 Aug 06:19**: started 955 symbols, wrote 114, stopped mid-line at 07:23,
  exited 1. Empty `.err`, no traceback despite stderr redirected to the log, no
  summary line. Disk 529GB free, no OOM/jetsam events, and the 5m lane ran
  cleanly through the same period (`lastexit=0`). **Cause not established.**
- **Contributing**: a broad seed on 24 Aug 21:35 took the us_etf tree from 250
  directories to 991, and `harvest_symbols()` unions universe with store, so the
  job silently quadrupled. Now bounded (fb6a014) — tonight runs 244 again.
- **Consequence for the data**: no daily bars were harvested by the scheduled job
  on either night. The 244 universe symbols are nonetheless current through
  24 Aug, because the 21:35 seed covered them. Verified: 0 missing, 0 stale.
- **Open**: the exit-1 mechanism. The scope bound makes tonight a smaller and
  therefore different test, so a clean run tonight will NOT prove the cause was
  scope.

### 2026-08-25 — ibkr_web volume was written 100x TOO HIGH (code fixed, data NOT repaired)
- **What**: the lot→shares ×100 was applied at TWO layers of one pipeline. I added
  it on 23 Aug to `IBKRResponseParser.ParseHistory` (C# — correct, that is where
  raw IBKR JSON lands) AND to `bar_cache/providers/ibkr_web_provider` (wrong —
  that provider reads `/api/integrations/ibkr/price-history` from our own
  backend, whose bars are already converted). Every row it wrote came out ×100.
- **Measured** against the endpoint the provider actually reads:
  `TXN 1d` API `2,212,121` vs parquet `221,212,100`. SPY's August bars stored
  **5.9 BILLION** shares/day against a real ~59 million.
- **Scope is PATCHY, not global — do not assume a uniform factor.** SPY median
  daily volume by month shows the mixture:
  - `ibkr`-sourced months (2025-05/08/10/12, 2026-01/02): 4.5–6.9bn — 100x high
  - `ibkr_web` months 2025-06/07/09/11 and 2026-03→07: 75–180M — roughly right
  - `2026-08`: 6.2bn — 100x high, and NEW, because that partition was re-sourced
    on 24/25 Aug through the now-doubling path
- **Code fixed** (this commit): the conversion happens only at ParseHistory.
  Verified — the provider now returns TXN 2,212,121, matching the API.
  Guarded by `tests/test_volume_lot_conversion_happens_once.py`.
- **Data NOT repaired, deliberately.** Volume feeds the universe's
  dollar-turnover floor and `build_universe` reads the parquet store, so a
  repair moves the universe mid-forward-test. Queued for the post-window store
  session (see ADJ_FACTOR_MIGRATION_PLAN.md) — now FIVE items, one session.
- **Does it affect the running test? No.** Swing reads `close`; it does not read
  volume. The universe is frozen at 244 and is not rebuilt during the window.
- **UNRESOLVED, and it needs the audit not a guess**: I could not produce a
  trustworthy universe-impact number. Two attempts gave different answers
  because the inflation is patchy by month AND source, and a 60-day median lands
  in different vintages for different symbols. Whether the 89→244 expansion was
  partly an artefact is an OPEN QUESTION, to be answered by the post-window
  audit rather than by inference. Nobody should quote a figure for it before then.

### 2026-08-23 — DELIBERATELY NOT CHANGED before the forward test: the adjusted/raw close seam
This is a **known condition carried into the test on purpose**, recorded here so
that "did the data change?" has an answer during the 12 weeks.

- **The condition**: the canonical store's `close` mixes conventions. Rows sourced
  from yfinance are dividend-ADJUSTED; rows from ibkr / ibkr_web are RAW.
  `adj_factor` is 1.0 for all 271 symbols, so nothing records which is which.
  **127 of the 244 universe symbols have a 200-day window that mixes both**, and
  the Swing rule gates every entry on price being above the 200-SMA.
- **Measured impact on tomorrow's gate: none.** Using each symbol's OWN dividend
  gap (not a flat assumption), the SMA200 understatement is median 0.241%,
  max 2.285%. **Zero symbols sit closer to their 200-SMA than their own bias**,
  so no entry decision flips. Closest calls: AXP −0.67% away with 0.18% bias,
  WFRD −0.91% with 0.09%. BRK-B looks like the nearest miss at 0.75% but pays no
  dividend, so its adjusted and raw series are identical (gap 0.000%) and its
  true bias is zero.
- **Why not fixed now**: a store-wide close rewrite hours before go-live is the
  same shape of change that took the API down for six hours this morning, and it
  would buy nothing measurable. The mixed rows are also the OLDEST part of the
  window (for most symbols a contiguous 2025-12 → 2026-02 block, 61 of 200), so
  they age out naturally as raw bars accumulate — the bias shrinks every week of
  the test rather than growing.
- **What would change this**: a symbol drifting to within ~0.25% of its 200-SMA,
  or a high-gap name (max bias 2.285%) doing the same. Re-run the check before
  concluding anything about a marginal entry.
- **The real fix, when the window allows**: **DECIDED 2026-08-23 by the owner —
  store RAW OHLC plus a populated `adj_factor`**, so both series stay derivable.
  Plan written up in `ADJ_FACTOR_MIGRATION_PLAN.md`. Not started, and
  deliberately not started: the provider change and the history backfill **must
  ship together**. Shipping the provider change alone would leave new bars raw
  against adjusted history — a second, fresher convention boundary sitting right
  at the live edge where the strategy reads, which is strictly worse than the one
  boundary we have now.

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

### 2026-08-25 — 142 symbols held 24 Aug TWICE, and one of the copies was corrupt
- **What**: the scheduled daily harvest died on 24 Aug (a `uv run` that had
  drifted above its `cd`; launchd's cwd made it ModuleNotFoundError). With no
  fresh daily bars, every lane fell through to yfinance and hit 247 rate-limit
  errors between 02:00 and 05:00 UTC. The bar-cache delta merge keys on the
  exact timestamp, and yfinance stamps a daily bar at 04:00 UTC where ibkr_web
  stamps 13:30 UTC — so the same session was stored twice, with two closes.
- **Symbols / range**: 142 symbols on 2026-08-24, plus an older vintage of the
  same mechanism (12 symbols across 2021-08-23 → 2021-08-30, the IBKR
  five-year-cap boundary week, and strays on 2026-08-12/13/14 and 2023-06).
- **Repair**: `cli/dedupe_bar_sessions.py --apply` — 158 partitions, 232 rows
  dropped, one row per session, golden source preferred. Store scans clean.
- **Prevention**: `bar_cache/store.py::_dedupe_sessions` keys the daily merge on
  the calendar session; the shrink guard now counts sessions, not rows.
- **Commit**: 1ea2428.

### 2026-08-25 — the stored ibkr_web daily bar for 24 Aug was WRONG on ~244 names
- **What**: found while checking why a Swing candidate appeared and vanished.
  TXN's stored bar read close 256.59 / low 256.19 against a true 258.94 /
  255.18 — a low ABOVE its own intraday low, which is impossible. IBKR's own
  API confirmed the true values independently. A forced re-source from the same
  provider returned the correct bar, so it was a bad WRITE, not a provider
  limit.
- **Why it mattered**: the error was 0.95%, and it was the whole difference
  between a 2.53σ Swing signal (fires) and 2.32σ (does not). The screen
  published a trade that did not exist. Corrupt data produced a FALSE POSITIVE
  on the screen; only the strategy's independent data path disagreed.
- **Symbols / range**: all 244 universe names, 2026-08 daily partition,
  re-sourced with `--force-refresh --ibkr-only`.
- **Prevention**: `cli/check_daily_vs_intraday.py` — every daily bar must
  contain its own RTH session, checked against the 5m lane we already hold.
  3,726 bars now check clean; 1 residual (D, 24 Aug, high only, closes match).
- **Commit**: 0e73317.

### 2026-08-25 — the live Swing strategy was trading 170 of its 244 names
- **What**: `_BUS_SYMBOL_CAP = 170` in `cli/paper_session.py`, sized when the
  universe was "large_50 ∪ high_beta ≈ 163". The universe became a committed
  244-name definition and the cap stopped covering it. The strategy started
  with 244 symbols, fetched bars for the first 170, and never evaluated the
  other 74 for entry — while the screen scanned all 244.
- **Affects**: the forward test from its first day. F1 ("live candidates match
  the committed harness") was comparing a 244-name screen to a 170-name
  strategy, and the ~7 signals/week rate assumed 244.
- **Fix**: cap now defaults to 400 (env-overridable) and any truncation logs at
  ERROR naming the dropped symbols. A coverage loss must never be silent.
- **NOT changed, needs an owner call**: the daily bars the strategy computes its
  signal from come from YAHOO (`profiles.py`, the `ibkr` broker branch uses
  `_yfinance_bus`; IBKR is used only for order routing). The screen and the
  entire backtest evidence base use IBKR bars from the store. Today those two
  disagreed on TXN and the strategy's Yahoo bar was the correct one. Changing a
  strategy's signal data source mid-window changes what the forward test
  measures, so it is flagged rather than flipped.

### 2026-08-25 — my 24 Aug re-source inflated stored volume 100x for August
- **What**: the force re-source that fixed the corrupt daily CLOSES also ran
  the volume through the double-conversion path the data lane diagnosed
  (6c22ebd): IBKR reports 100-share lots, and the conversion is applied both
  in `IBKRResponseParser.ParseHistory` and again in `ibkr_web_provider`, which
  reads our own already-converted API. Stored SPY August volume reads ~6.2bn
  shares/day against a real ~59m.
- **Symbols / range**: all 244 universe names, 2026-08 daily partition. The
  inflation is PATCHY across the store by month and source vintage, so a flat
  `/100` repair is NOT correct — see the data lane's month table.
- **Not repaired**: `build_universe` reads the parquet store, so repairing
  volume moves the universe mid-forward-test. Queued for the post-window audit.
- **Does not touch the running test**: mean reversion reads closes only, the
  universe is frozen at 244, and `poison_check` keys on volume being ZERO,
  which no scale factor changes.
- **Does touch what the screens PUBLISH**: `volume_vs_20d` is a ratio, and a
  ratio only cancels a UNIFORM error. The 20-session window currently spans
  2026-07-28 (correct) to 2026-08-24 (inflated). It reads 0.95–1.41 today —
  plausible, biased ~17% high. Once correct bars arrive it reads 0.011, which
  renders as a 99% volume collapse on every symbol at once.
- **Fix**: `universe.volume_ratio()` — ONE implementation, imported by both
  screens (each previously had its own copy), which locates the largest
  adjacent step in the window, splits there, and withholds the ratio when the
  two sides differ by >=20x. The desk shows "withheld" with the reason rather
  than a dash. Commit: see below.
