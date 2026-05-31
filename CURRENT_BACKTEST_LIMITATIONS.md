# Current backtest + simulation limitations

> **Read this before trusting any backtest number TradePro produces.**
>
> This document is the honest, trader-readable answer to "what can I
> believe about TradePro's historical evidence?". Every limitation
> below is real today; each one carries a severity, a concrete
> consequence, and the remedy that's on the roadmap.
>
> A trustworthy system tells you what it doesn't know. That's the
> spirit here.

Last reviewed: 2026-05-29
Companion: `ROADMAP.md` → "Phase: Trustworthy data layer"
Visible in UI: Settings → Data Health & Provider Preferences

---

## TL;DR

| Strategy type | Backtest trust level | Why |
|---|---|---|
| **Daily strategies** (`ichimoku_equity`, daily Ichimoku scorer) | ✅ **Roughly trustworthy** within ~1–2% | Yahoo daily back to ~2000 is stable + adjusted close is clean |
| **Intraday strategies** (`intraday_flat`, `orb`, `vwap_mean_reversion`, `bollinger_bounce`, `ma_crossover`) at 1m resolution | ❌ **Effectively fictional past 7 days** | Yahoo's 1-minute history ceiling is 7 days; no other free provider in the chain |
| **FX strategies** (`ichimoku_fx_mr`) at hourly resolution | ⚠️ **Partial** | Yahoo hourly works but FX providers disagree on the same pair by basis points |
| **Slippage assumptions everywhere** | ❌ **Universally optimistic** | All backtests fill at OHLC close. No bid/ask history. Reality: 1bp on liquid ETFs (small), 5–15bp on single names (material). |
| **Live-vs-backtest fill comparison** | ❌ **Impossible** | No historical bid/ask means no way to compare what a backtest predicted vs what IG/T212 actually filled |
| **Strict reproducibility** | ❌ **Weak across all** | yfinance silently revises historical bars; two runs a month apart can disagree by a few bps |
| **LLM gate in any historical backtest** | ❌ **Anachronistic** | The gate fetches CURRENT news and applies CURRENT sentiment to historical entries. Disabling it for backtest creates an honesty gap against live; enabling it creates a different lie. |

If you take one thing from this doc: **your intraday strategies have
never been backtested at meaningful historical depth. They have only
been forward-tested for ≤ 7 days at a time.** That's a hard ceiling
imposed by free-tier data, not a TradePro choice — but it's a real
constraint on what you can claim.

---

## Detailed limitations

### L1 — Intraday data ceiling (CRITICAL → DOWNGRADED to HIGH after Phase B-4)

**Status update (Phase B-4)**: IG `/prices` is now wired as a second
provider in the BarStore chain. For symbols with a populated IG epic,
the operator can backfill multi-year 1-minute history via the
trustworthy bar cache. The CRITICAL framing applied while no provider
covered the gap; with IG in the chain the limitation is **bounded by
operator action** (populate epics + run backfill), not by a hard data
unavailability.

**What's true (post B-4)**:
- Yahoo Finance 1-minute history is still capped at the last 7 days.
- Yahoo 5m/15m/30m: capped at 60 days.
- Yahoo 1h: many years available.
- **IG `/prices/{epic}` now reachable via the BarStore chain** — multi-
  year intraday history available for any symbol whose IG epic is
  populated in `paper/ig_epic_map.json`. Demo accounts have a weekly
  bar-download allowance (~10k datapoints/week typical); the IGProvider
  bubbles 403 responses up as `ProviderRateLimitError` so the chain
  falls back to yfinance.
- The BarStore writes IG-supplied bars to the same Parquet partitions
  as yfinance-supplied bars (same `us_equity_v1` schema). Manifest
  records `provider_used: "ig"` for honesty about provenance.

**Consequence (residual)**:
- For symbols with NO populated IG epic, the 7-day ceiling still
  applies — yfinance is the only configured fallback.
- The IG demo weekly allowance bounds how much history can be
  backfilled per week. A trader populating SPY/QQQ/IWM intraday
  history for 2024 has to spread the backfill across weeks or burn
  the entire allowance in one sweep.
- Live + backtest divergence on slippage / spread still applies
  (see §L2).

**Remedy (on the roadmap)**:
- ✅ Phase B-4 SHIPPED: IG `/prices` provider wired in. Migration 033
  seeds `us_etf 1m` chain to `['yfinance', 'ig']` so the fallback is
  live by default.
- Phase C: backfill CLI + UI button (Settings panel) lets trade
  support populate the cache without SSH/CLI.
- Phase E: refuse to run a backtest whose data is incomplete, with a
  clear remediation message pointing at the backfill button.

**Mitigation today (post B-4)**:
- For symbols with IG epics populated: `tradepro-bar-cache-get
  --api-base http://localhost:5252 --canonical SPY --asset us_etf
  --resolution 1m --from 2023-01-01 --to today` triggers the full
  backfill. Subsequent backtests of intraday strategies read the
  cached bars — honest 1-year history is back on the table.
- For symbols WITHOUT IG epics populated: same 7-day ceiling as
  before; run `tradepro-ig-populate-epics` to fill the gap.
- Watch the cockpit's "Bar cache activity" panel for
  `result=rate_limited` events: that's the IG weekly allowance
  signalling — wait for the reset or fall back to daily resampling.

---

### L2 — Slippage is fictional (HIGH)

**Severity**: HIGH — meaningfully biases reported returns upward.

**What's true**:
- All backtests assume fills at the bar's `close` price.
- No historical bid/ask spread is stored anywhere.
- IG / T212 supply bid/ask only at the moment of order placement, not historically.

**Consequence**:
- Backtest returns are systematically optimistic by the realised spread cost.
- Magnitude:
  - Liquid US ETFs (SPY, QQQ, IWM): ~1bp per round-trip. Small but non-zero.
  - Liquid single names (AAPL, MSFT, GOOG): ~3–5bp. Becomes material across many trades.
  - Less-liquid names (small-caps, foreign ADRs): 10–50bp. Can flip a strategy's sign.
  - Wider on 1-minute bars vs daily because intraday spreads are wider than EOD reference.

**Remedy**:
- Phase F (post-data-layer): store IG L1 bid/ask snapshot at every fill; build an empirical slippage model from realised vs theoretical fills.
- Phase G: ship a simple "spread haircut" model (% of mid) as an opt-in backtest mode so analysts can see "what does the backtest look like with 2bp/4bp/10bp spread applied?".

**Mitigation today**:
- Apply a manual haircut when comparing backtest results to live: subtract 1bp for ETF backtests, 5bp for single-name backtests, more for illiquid.
- Treat any backtest Sharpe within ~0.3 of the threshold-of-interest with skepticism — that's well within the slippage error bar.

---

### L3 — LLM gate is anachronistic in backtests (HIGH)

**Severity**: HIGH — backtest and live are not the same system.

**What's true**:
- `LLMSignalGate.evaluate(symbol, ...)` fetches today's news headlines and scores today's sentiment, then returns APPROVED/VETOED/APPROVED_BOOSTED.
- A backtest replaying a 2024 entry would call the gate today; the gate would fetch news from today; the decision would be based on today's sentiment, not the sentiment that existed at the historical entry moment.
- Two compensating choices, both wrong:
  - **Gate enabled in backtest** → historical entries are evaluated against modern news. Backtest is a lie.
  - **Gate disabled in backtest** → live behaviour includes a gate effect that the backtest doesn't capture. Live diverges from backtest by an unknown amount.

**Consequence**:
- No correct way to backtest the LLM gate's contribution.
- Going forward: every live LLM call now persists to `llm_evaluations` (migration 022), so prospective replay becomes possible eventually.
- Retroactively: impossible. The news + sentiment for 2024 entries was never stored.

**Remedy**:
- Phase H: backtest mode for LLM gate replays the stored `llm_evaluations` rows by timestamp — honest from the date `llm_evaluations` started capturing data (≈ 2026-05-28).
- Phase H+1: cohort analyses splitting "backtest with LLM gate disabled" vs "live with LLM gate active" to measure the gate's contribution empirically.

**Mitigation today**:
- Backtests of `ichimoku_equity` and `ichimoku_fx_mr` are run with `LLMGateConfig.enabled=False`. The cockpit must show that flag clearly on every backtest result.
- For live trading, expect a delta from the backtest number equal to whatever the gate vetos / boosts in practice — currently unmeasured.

---

### L4 — Reproducibility is weak (MEDIUM)

**Severity**: MEDIUM — undermines walk-forward + Monte Carlo confidence.

**What's true**:
- yfinance silently revises historical bars (corporate actions, late dividend reinvestments, split adjustments).
- Two backtest runs of the same strategy on the same period, executed weeks apart, can disagree by a few bps.
- No backtest result currently records the data state it used.

**Consequence**:
- Can't reliably compare a backtest result from 2024 with one from today.
- Walk-forward sweeps that depend on stable historical data have a noise floor higher than zero.
- A team member running the same backtest as another team member can get different numbers; the diff isn't a bug, it's data drift.

**Remedy**:
- Phase D: stamp every backtest result with `data_provider`, `data_provider_version`, `bar_count_per_symbol`, `bar_partition_hash`. Reruns with matching hashes MUST produce identical numbers.
- Phase D+1: result viewer surfaces the data hash; "this matches the 2024-08 run" vs "this used different bars".

**Mitigation today**:
- For a definitive comparison, rerun both legs of a comparison on the same day with the same cache state.
- Treat backtest "trends over months" with appropriate humility: the data underneath has been silently rewritten.

---

### L5 — No fill-quality audit (MEDIUM)

**Severity**: MEDIUM — live divergence has no diagnostic.

**What's true**:
- When `intraday_flat` (or any strategy) fills on IG / T212, we record the broker's fill price.
- We do not record the bid/ask at the moment of fill, the broker's depth ladder, the time-to-fill, or the slippage vs mid.
- So "did we get a fair fill?" is unanswerable from data.

**Consequence**:
- A broker fill at a noticeably worse price than mid goes undetected.
- Strategies that look broken in live could be data-layer broken (we filled poorly), not signal-layer broken.

**Remedy**:
- Phase F: store IG L1 snapshot at every fill (new column on `oms_fills`).
- Phase F+1: per-fill spread/slippage analytics in the cockpit.

**Mitigation today**:
- Spot-check a sample of fills against the IG demo UI for the same symbol/time, manually compare to mid.

---

### L6 — Yahoo rate limits silently corrupt sweeps (MEDIUM)

**Severity**: MEDIUM — a backtest sweep over many symbols can have silent holes.

**What's true**:
- yfinance returns HTTP 429 or empty frames when rate-limited.
- The current strategy code treats an empty frame as "no signal", not "data missing".
- A cross-sectional backtest over 100+ symbols at 1m resolution will often have a non-trivial fraction of symbols come back empty.

**Consequence**:
- Symbol X's "no signal today" might mean "real no signal" or "yfinance rate-limited" — we don't distinguish.
- The cross-sectional ranking is biased toward symbols that fetched OK.

**Remedy**:
- Phase A: per-fetch telemetry distinguishes 429 / empty / parse-error / network-error / OK.
- Phase B: cache layer absorbs the rate limits — same-symbol same-window only hits the network once.

**Mitigation today**:
- Run sweeps during off-peak hours (early UTC).
- After any cross-sectional run, sanity-check sample symbols against a manual yfinance call.

---

### L7 — DST / holiday boundaries (LOW)

**Severity**: LOW — but creates occasional off-by-one in intraday entries.

**What's true**:
- US DST shifts in March and November shift the UTC-to-ET mapping by an hour.
- Strategy timing windows (entry, flatten, close) are configured in UTC.
- A strategy configured with `entry_window_start_utc="13:35"` opens at 09:35 ET during DST, 08:35 ET during standard time. Either the strategy was tuned for DST and is wrong half the year, or vice versa.

**Consequence**:
- An intraday strategy may enter at a different relative-to-open time across the year.

**Remedy**:
- Phase B+1: switch timing windows from UTC to exchange-local time with explicit timezone handling.

**Mitigation today**:
- Audit each intraday strategy's timing across a DST boundary.

---

### L8 — Asset-class coverage is currently equity + FX only (INFORMATIONAL)

**Severity**: INFORMATIONAL — not a flaw, just a current scope.

**What's true**:
- TradePro currently sources data for cash equities, ETFs, and FX spot.
- No infrastructure exists for options chains, futures, or crypto.
- The cache layer (when built) needs to be asset-class-aware because schemas diverge sharply (options chains: snapshot per expiry × strike; futures: contract-rolling; crypto: 24/7 with multi-venue).

**Consequence**:
- Strategies that need options or futures data cannot be built today.
- This is not a limitation of what exists, but a constraint on what can be added.

**Remedy**:
- Phase B is designed asset-class-pluggable from day 1. Adding a new asset class will be a single-file plugin, not a schema rewrite.

**Mitigation today**: N/A.

---

## What the UI shows

**Settings → Data Health & Provider Preferences** (introduced in Phase A):

1. **Limitations panel** (this doc, rendered as accordion):
   - One row per limitation, severity colour-coded.
   - Click row → detail with consequence + remedy + mitigation.
   - Stale-review badge: limitation last reviewed > 30 days ago = highlight for re-review.

2. **Data assumptions registry**:
   - Each assumption gets a row: ID, description, current status (HONEST / PARTIAL / OPTIMISTIC / FICTIONAL), severity, remedy.
   - Editable so the assumption list grows with reality.

3. **Provider preferences** (editable):
   - Per (asset_class × resolution), the provider chain order.
   - Defaults: equity 1d → yfinance, equity 1m → yfinance (limit acknowledged), FX 1h → yfinance.
   - As new providers are added, they appear here.

4. **Backfill button** (Phase A: placeholder; Phase C: functional):
   - Per symbol × resolution × range.
   - Clear "Phase C, not yet implemented" badge for now.

---

## Operational model — what trade support can trigger from the UI

A trustworthy data layer is also one where **a non-engineer operator
can fix things from the cockpit**. Every routine data operation lives
behind a UI button, with audit and confirm-prompts proportional to
the destructive power of the action. SSH / CLI access is for
emergencies, not normal ops.

### Substrate

Reuses the existing `session_requests` queue + `/api/ops/*` endpoint
family (same pattern as intraday + paper sessions). No new
infrastructure. A new `tradepro-data-worker` daemon mirrors
`tradepro-intraday-engine` and polls for `data_*` kind rows.

### Registered operations (Phase B/C/D rollout)

| Operation | Phase | Destructive? | UI confirm | Worker action |
|---|---|---|---|---|
| **Validate** (`data_validate`) | B | No | Quick confirm | Walks manifests, reports gaps + integrity violations |
| **Backfill missing** (`data_backfill`) | C | No (additive) | Confirm with payload preview | Pull bars for (symbol, resolution) over date range, write atomically |
| **Reload** (`data_reload`) | C | Yes (overwrites) | Confirm modal + reason text required | Force re-fetch, overwrite existing partition |
| **Repartition** (`data_repartition`) | D | Yes (rewrites) | Confirm modal + reason | Rewrite Parquet to a new schema version |
| **Purge** (`data_purge`) | D | Yes (destroys) | Confirm modal + reason + 2nd "I understand" check | Remove partition (provider revision known-bad) |

### Audit trail per operation

Every enqueued op records:
- `request_id` (UUID)
- `requested_at_utc`, `requested_by` (auth context)
- `params` (full payload — symbol, range, resolution, etc.)
- `reason` (operator-supplied text, mandatory for destructive ops)
- `state` (Pending / Claimed / Completed / Failed / Cancelled)
- `claimed_by` (worker hostname / instance_id)
- `result_summary` on completion:
  - `rows_written`, `provider_used`, `partition_hash`, `latency_ms`
  - Honest "what changed" so cockpit shows "12 480 bars written for SPY 2024-06"
- `error` + `retry_strategy_hint` on failure

### Where this maps in the UI

- **Phase B**: Validate button per row in Data Health panel.
- **Phase C**: Backfill + Reload buttons per row, with job status
  inline (Pending → Claimed → Completed badge that updates via poll).
- **Phase D**: Coverage matrix becomes click-to-backfill, with a
  bulk-select for multi-cell ops.
- **Phase G**: Coverage matrix shows complete / partial / missing
  per (symbol × month) — every cell has its own action menu.

### Why one worker, not a separate service

A separate "data-loader" service was considered and rejected:
- Existing `session_requests` queue + ops pattern works perfectly.
  Reusing it = zero new infra, zero new deployment, familiar audit
  surface.
- Mac is already the right place to run it: yfinance / IG / T212
  credentials live there, the file system for Parquet partitions
  lives there.
- If we ever need to scale beyond one worker, the queue already
  supports multiple claimants via the atomic UPDATE-RETURNING claim
  (`tradepro-data-worker --instance-id <unique>`). N workers, one
  queue, no schema change.

### Operator safety rails

- **Confirm modals** are proportional to destructive power. Validate
  is one click. Purge is two clicks + reason text + "I understand"
  checkbox.
- **Dry-run preview** for destructive ops shows what would be
  overwritten / deleted before the enqueue lands.
- **Cancellable**: POST `/api/ops/sessions/{id}/cancel` works on
  Pending rows; once Claimed the worker is responsible for
  cancellation cooperation.
- **Rate-limit guard** in the worker (token bucket per provider)
  ensures a stampede of operator-triggered backfills doesn't blow
  the yfinance / IG quota.
- **Replay-safe**: a worker crashing mid-op leaves the
  `session_requests` row in `Claimed` state with a stale
  `claimed_at_utc`; a watchdog (Phase D) re-queues rows claimed for
  too long, so operator-triggered ops are crash-tolerant.

---

## What this means for the project north-star

The north-star is:

> BUY/SELL/HOLD recommendation across ETF universe, evidenced by
> backtest + stress-scenario impact.

Translation through this doc:

- **For daily ETF BUY/SELL/HOLD from `ichimoku_equity` or daily scorers**: north-star is satisfied at a defensible quality level. Backtest evidence is real, stress scenarios runnable.
- **For intraday execution of those signals via `intraday_flat`**: north-star is **partially blocked** until Phase B/C/E ship. The strategy can run live and accumulate forward-test evidence; historical evidence is not currently producible.
- **For the system as a whole**: trustworthy backtest output requires the data layer roadmap below to ship at least through Phase D (reproducibility hashing). Phases B + C + E together unlock honest intraday backtesting.

---

## The shortest possible summary

If a future investor or trader asks "what's TradePro's track record":

- For daily strategies: "Backtested on 10+ years of daily Yahoo data; numbers within ~1–2% of reality due to slippage and data-revision drift."
- For intraday strategies: "No historical track record. Strategy logic is unit + BDD verified. Live forward testing began [date]."
- For the LLM gate's contribution: "Currently unmeasured. Will be measurable once the data layer's replay capability ships."

That's the honest answer. This doc exists so it stays the answer
until the remedy ships.
