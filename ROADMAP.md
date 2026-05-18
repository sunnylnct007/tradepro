# TradePro Roadmap

A personal, evidence-based trading-strategy platform. The product
question:

> **"Today, should I BUY / WAIT / AVOID, and which ETF?"**
> — backed by backtest evidence, per-regime stress survival, and a
> cross-check against analyst consensus.

This document is the load-bearing plan: where we are, where we're
going, and the assumptions baked into every choice. Update it when
those assumptions change.

---

## Recently shipped (May 2026)

Tracks meaningful work that's already in `main` so this doc stops drifting
out of date. Each entry is one line: what changed and why it mattered.

**Week of 2026-05-18 — unicorn-grade foundations:**

- ✅ **Phase 5 — Postgres migration of every store**. All 9 in-memory +
  JSON-file stores (pending orders, paper snapshots, paper backtests,
  paper strategies, watchlists, settings, compare cache, documents,
  heartbeats) now backed by Postgres on the same EC2 host. Survives
  redeploys. Schema also seeds the future event-sourcing tables.
- ✅ **Phase 6 — Event-sourced orders + fills + domain log**. Every
  paper-trading order writes to the append-only `orders` table with a
  `decision_trace`; risk decisions (approve / reject) leave an
  audit trail; `events` table seeds the Phase 7 SSE stream. Read API
  at `/api/orders/`, `/api/orders/{id}`, `/api/events/`.
- ✅ **AWS Secrets Manager bundle**. Single `tradepro/all` JSON in
  eu-north-1, read by both the Mac engine and the .NET API at boot.
  Cross-region IAM provisioned via the ccit-infra TF module.
- ✅ **Daily email digest plist** (`com.tradepro.email-digest`,
  23:00 UTC) + dry-run + real-send verified end-to-end.
- ✅ **Daily paper-trading plist** (`com.tradepro.paper`, 14:30 UTC).
  Reads symbol list from env, omits `--placement-mode` so the engine
  resolves it from `/api/settings` per run.
- ✅ **UI toggle for paper-trading placement mode**. Settings page
  has Auto / Manual pills wired to `/api/settings`; the Mac engine
  fetches the value at session start. Survives every redeploy via the
  Postgres settings store.
- ✅ **HOLD-IN / HOLD-OUT split** on the Backtest page so
  `1 BUY + 0 SELL + 3 HOLD-IN + 3 HOLD-OUT` reconciles in-place with
  the "4 of 7 currently long" line — math now adds up at a glance.
- ✅ **STRATEGIES.md** — canonical reference for all 3 strategy layers
  (.NET signal, Python paper, horizon scorers) including the
  instrument-strategy fit problem (MTUM/RSI-MR case).
- ✅ **EVALUATION.md** — the five lenses for telling whether a
  strategy is working on a given symbol, with the AVGO worked example.
- ✅ **VISION.md** — unicorn-architecture target: 8 properties + 6 PR
  principles + 8-phase arc.
- ✅ **aws-redeploy.yml SSM output truncation fix** — pull/up output
  routed to /tmp logs, only the tail shipped back. Stops the workflow
  reporting "failed" on every successful deploy.
- ✅ **Strategy leaderboard on Decide page + MCP tool**. Per-symbol
  ranking of all 7 strategies by Sharpe, with action labels collapsed
  to BUY / SELL / HOLD-IN / HOLD-OUT and a delta vs the buy-and-hold
  null model. Frontend widget on the expanded panel; MCP exposes the
  same shape via `get_strategy_leaderboard(universe, symbol)` so an
  LLM agent sees the same ranking a human does. Closes the user's
  "how do we see which strategy is handling that symbol better"
  question.
- ✅ **Phase 6.5 — instrument-strategy fit**. Closes the MTUM
  contradiction. Every symbol classified via `factor_types.py`
  (momentum / value / quality / low_vol / broad_equity / bond /
  commodity / crypto / single_stock / ...); the consensus engine
  excludes structurally-incompatible strategy votes (RSI mean-
  reversion on MTUM, breakouts on USMV, etc.). UI banner explains
  exclusions; leaderboard greys out excluded rows. New MCP tool
  `get_instrument_fit(symbol)` returns the classification + the
  list of compatible / incompatible strategies with a one-line
  reason per factor type. STRATEGIES.md updated.

**Week of 2026-05-10 — chart depth + rationale precision:**

- ✅ **Inline `PriceHistoryChart`** on Research + Decide pages — 5y
  split-adjusted line + SMA(200) overlay + 52w high/low reference.
  Range presets (1M / 3M / 6M / YTD / 1Y / 5Y / All), recharts `Brush`
  for free-form zoom, "today" marker, dip/near-highs range zones,
  volume strip below sharing the brush, dated dots at the 52w extremes
  so the user can read live-vs-stale floor at a glance.
- ✅ **Rationale prompt v3 → v4** — guard against the "N/A as
  single-stock analysis" hallucination for ETFs (v3); ban round-number
  RSI/SMA filler, ban LLM-computed percentages, ban SMA(50), restrict
  year mentions (v4). Cache audit: 35% of LLM rationales were getting
  rejected; v4 targets the recurring offenders.
- ✅ **Verifier enrichment** — verification notes now carry the
  offending sentence (`unsupported number: 999% — in sentence: "..."`)
  so the user can see *which claim* to discount, not just the bare
  number. Frontend pill goes amber ⚠ when notes > 0 instead of the
  misleading green ✓.
- ✅ **`closes_30d` field on `MarketState`** — wires up the
  already-committed `email_charts.buy_sparklines_png()` so BUY rows in
  the daily digest get a 30-bar mini-chart per name.

**External reviewer suggestions (2026-05-11) — triage:**

- ✅ **Already shipped** — reviewer's #5 (risk rating, commit `61bb7e5`) and #10
  (Gem Hunter, commits `57a9c3b` + `ac7a55b`). Visible as `RiskPill` and
  `GemsCard` on the Decide page. Skip re-prioritising.
- 🟢 **Validates existing direction** — #1 data persistence (Phase D, below),
  #3 historical P/E store (Phase B / D2 snapshot store), #7 Monte Carlo
  (Phase B), #8 Lambda+EventBridge worker (Phase D3), #9 live price feed
  (Phase 7). Already on the roadmap; reviewer's priority weighting noted.
- 🆕 **New, accepted** — added below:
  - ~~**Symbol autocomplete**~~ — already shipped: SymbolPicker.tsx has
    full debounced typeahead against `/api/instruments/search` (Yahoo
    search), keyboard navigation, T212 badge for tradeable symbols.
    Reviewer wasn't aware.
  - **Insider trades + analyst recommendations layer** — `yfinance.Ticker
    .insider_purchases` + `finnhub.stock.recommendations`. New signal family
    on the swing scorer (currently 5 layers, would become 6). Lives in
    `tradepro_strategies/insiders.py`. Decision trace gains an "insider"
    row. ~1-2 days. Notes: be careful about false positives — automatic
    10b5-1 plan sales/buys look like discretionary insider trades but
    aren't; filter on `acquisition_or_disposition == 'D'` correctly.
  - **S3-archive-in-push** — the `tradepro-archive` terraform module
    (S3 bucket + writer creds) is provisioned but `push_to_api.py` doesn't
    upload there yet. Add an opt-in `archive_to_s3()` call alongside the
    API push so we have replay history before Postgres lands. ~2 hrs.
- ❓ **Deferred — needs decision** — #2 earnings markers + corporate-actions
  markers on chart (already in flight queue below; the reviewer's pairing
  it with #4 insider trades raises the question of whether to land them as
  one "events-on-chart" PR — see backend-endpoint follow-up below).

**Stress-test reviewer suggestions (2026-05-11) — triage:**

Worth noting up front: TradePro is a **research / signal tool**, not a
live execution platform. The reviewer's risk-management suggestions are
about **backtest realism** (would change the historical CAGR / Sharpe
numbers) rather than runtime trade safety — users decide whether to
buy on their own. That framing changes the priority of each item.

- ✅ **Already shipped** — reviewer's "What's Working" list matches
  current state: range-position guard (VUKE class), 5y peak context
  (INRG class), two-tier sentiment demotion, decision trace, fee +
  stamp-duty model, NaN/inf safety, v4 rationale prompt.
- 🟢 **Validates existing direction** —
  - **Sideways-market volatility filter** — partially handled today by
    the swing composite's earnings-event layer + sentiment demotion;
    a hard `annualised_vol < 15% → HOLD` rule could fit in the
    `_classify` ladder but risks suppressing valid mean-reversion
    setups on low-vol ETFs (VUKE / VGOV are <12% vol and BUY-able).
    Skip unless we see whipsaw losses in the cache.
  - **LLM hallucination rate** — already targeted by v4 prompt rollout;
    re-audit after worker has pushed under v4 for a week.
- 🆕 **New, accepted into roadmap** — added below:
  - **Backtest stop-loss option** (HIGH) — trailing stop + max-loss-per-trade
    as `BacktestConfig.StopLossConfig` flags, default off so existing
    suite stays comparable. Lets the user A/B "buy_and_hold vs same
    strategy with 8% trailing stop" in the Backtest UI. ~1 day.
  - **Crash-protection branch in `_classify`** (MEDIUM) — new rule:
    `10-day return < -8% AND below SMA200 → AVOID with "active crash"
    reason`. Catches the "falling knife" gap reviewer flagged. Sits
    *before* the bounce-zone BUY rule so a confirmed crash always
    wins over a mean-reversion bid. New scenario in
    `features/market_state_classify.feature`. ~3 hrs.
- ❓ **Deferred — needs design**:
  - **Position sizing (Kelly / fixed-fractional / vol-targeted)** — touches
    the backtest engine, the swing composite, and the (future) portfolio
    simulator. Better to land alongside Phase B (portfolio simulation)
    than as a standalone change. Logged here so it isn't lost.
  - **Correlation filter** — same: belongs in Phase 2 (portfolio-aware
    engine), which already plans buy-more / hold / trim across positions.
    Logged in `project_phase2_portfolio_aware` memory.

**In-flight / next up (do not lose):**

- ✅ **AWS deploy — LIVE** as of 2026-05-11. Tradepro is on its own
  t4g.small at `http://16.60.201.137/` (instance
  `i-01b390204472e4b9f`, EIP preserved across stop/start). Frontend
  on port 80 with nginx proxying `/api/*` to the .NET API container,
  plus port 8081 still exposed for direct API debugging. Auto-stop
  at 22:00 UTC, auto-start 08:00 UTC weekdays.
  - Terraform module: `~/sourcecode/ccit-infra/modules/tradepro-demo/`
  - Deploy workflows: `.github/workflows/aws-{bootstrap,build-push,
    redeploy,set-env,start,stop,status}.yml`
  - Operator guide: `docs/aws-deploy.md` · architecture:
    `docs/aws-architecture.md`
  - Follow-up: rotate the PAT in SSM (`/ccit-dev/tradepro/github-deploy-pat`)
    — current one is invalid. Bootstrap workflow ships the compose
    file via runner checkout so it's not strictly needed, but
    `aws-redeploy.yml` still git-fetches on the box and will fail
    until the PAT is fresh. Either rotate the PAT or refactor
    redeploy to use the same checkout-and-ship pattern.
- ⏳ **Events-on-chart bundle** (earnings + corp actions + insider) — three
  related backend endpoints + matching chart markers. Land as one PR so
  the chart's "event overlay" layer ships coherently.
  - `GET /api/marketdata/earnings?symbol=&from=&to=` → vertical lines for
    earnings dates from `yfinance.Ticker.earnings_dates`. Surfaced by
    `tradepro_strategies/earnings.py:fetch_recent_earnings` already.
  - `GET /api/marketdata/corporate-actions?symbol=` → split + dividend
    events from `yfinance.Ticker.actions`. Small "S" / "D" markers.
  - `GET /api/marketdata/insiders?symbol=` → insider buys/sells from
    `yfinance.Ticker.insider_purchases`. Tiny up/down chips on the chart;
    filtered to discretionary trades (drop 10b5-1 plan executions).
- ✅ **Symbol autocomplete** — shipped (SymbolPicker.tsx →
  /api/instruments/search → Yahoo). Includes debounce, keyboard nav,
  T212 "tradeable" badge. Cures the "NV → 500" footgun.
- ⏳ **S3 archive in push pipeline** — `tradepro-archive` bucket exists
  (terraform `modules/tradepro-archive` + writer creds in outputs); the
  push CLI doesn't upload there yet. Add `archive_to_s3()` after a
  successful `/api/ingest/compare` so we have replay history before
  Phase D2 lands. Opt-in via `TRADEPRO_S3_ARCHIVE=1` env. ~2 hrs.
- ⏳ **Backtest stop-loss option** — new `BacktestConfig.stop_loss`
  block (trailing pct + max-loss pct). Default OFF so existing 187
  scenarios remain reproducible. Wire into `run_backtest.py` exit
  logic + add a UI toggle on the Backtest page. ~1 day.
- ⏳ **Crash-protection rule in `_classify`** — `10d return < -8% AND
  below SMA200 → AVOID ("active crash")`. Placed BEFORE the bounce-zone
  BUY check so a confirmed crash always wins. New behave scenario:
  `Given a series in active 10d crash, expect entry_signal AVOID`.
  ~3 hrs.
- ⏳ **Re-audit rationale cache after v4 prompt rolls out** — target
  rejection rate <10% (currently 35%). If v4 doesn't move the needle,
  the next move is a *model* audit: which model is producing the
  round-number filler? Bigger / instruction-tuned models hallucinate
  these less.
- ⏳ **UI smoke test of `PriceHistoryChart` enhancements** — the four
  chart commits (`1dc3186`, `d5e94e7`, `de92e7e`, `135e687`) shipped
  with `tsc --noEmit` clean but no browser run. Smoke before next
  deploy: zoom presets work, brush handles drag, today marker visible,
  range zones tinted correctly, volume strip syncs to the brush.
- ⏳ **`build/`, `tsconfig.tsbuildinfo`, `.idea/` removal from git
  history** — added to `.gitignore` in `2b74a26` but if they were
  previously committed somewhere, a follow-up `git rm --cached` may
  be needed (probably weren't — was the first time we noticed them).

**Week of 2026-05-06 → 2026-05-09 — horizon engine + portfolio surface:**

- ✅ **Range-position guard on BUY** (`market_state.py`) — VUKE-class
  fix. Symbols at ≥70th pctile of 52w range get downgraded BUY → HOLD;
  ≥80th pctile is hard-capped at WATCH. Covers the "5% off the 52w
  high after a +24% YoY run is not a dip" case the live VUKE.L test
  surfaced.
- ✅ **Horizon Classification Engine** (`horizons.py`,
  TRADEPRO-SPEC-001 §6) — three independent verdicts per symbol:
  swing (1–8w), long-term (6–18m), passive (3–5y). Each has its own
  0–8 score, signal grade and reasons. Wired into compare payload as
  `horizon_classification` + new MCP tool `get_horizon_signals(symbol)`.
- ✅ **Trading 212 portfolio surface** — live-mode auth fix (single-key
  vs Basic), 30s positions cache to dodge T212's 1-req/1s rate limit,
  `HoldingsHealthCard` on the Decide dashboard, full `Portfolio` page,
  email digest "What you hold" section. New `get_portfolio_signals`
  MCP tool returning per-position BUY_MORE / HOLD / TRIM with narrative.
- ✅ **Email digest charts + PDF attachment** — bucket donut, holdings
  P&L bar, BUY-candidate sparklines inline in HTML body. Multi-page
  PDF with cover, methodology, holdings, per-symbol detail (decision
  trace + horizon scores + sentiment + analyst targets), glossary.
- ✅ **Two-tier sentiment demotion** (`compare.py`) — mean ≤ −0.45 with
  ≥3 material-negatives demotes any bucket → AVOID, distinct from the
  existing −0.30 → WAIT. Differentiates "news backdrop is bad" (WAIT)
  from "news flow is genuinely hostile" (AVOID).
- ✅ **P/E hybrid valuation lens** (`cross_sectional.py`) —
  `bucket_by_valuation` orchestrator picks P/E quartiles for stock
  baskets (NVDA no longer mis-flagged "expensive" purely for not
  paying a dividend), falls back to dividend-yield quartiles for ETF
  baskets where P/E isn't reported. Honest gap: still BASKET-relative,
  not vs symbol's own historical median (snapshot store parked).
- ✅ **AVOID demotion on extreme negative sentiment** (above) +
  **AMZN-class fix** for sentiment confused-with-WAIT.
- ✅ **Worker pushes finally landing** (`push_to_api.py`) — credentials
  loader fell back to env vars when `~/.tradepro/credentials` is
  absent (docker worker had `TRADEPRO_API_URL` + `TRADEPRO_API_TOKEN`
  set in compose env but the loader exited early). Heartbeats now
  reach the api over the compose network.
- ✅ **Finnhub forward-earnings calendar wired** — `FinnhubEarningsEvent`
  parsing fix (Quarter/Year are JSON ints not strings); EPS-warning
  copy in the digest now fires when a holding has earnings within 14d.
- ✅ **MCP tool reliability** — `_get` default timeout 10s → 30s,
  `get_portfolio_signals` parallel-fetches universes; tunable via
  `TRADEPRO_MCP_TIMEOUT` env. Stops Claude Desktop's visualiser from
  giving up mid-tool-call.
- ✅ **UX polish** — Decide is now the index route (Scanner moved to
  `/scanner`); Mac → Strategy Engine rename in user-visible strings;
  Backtest page gets the existing `SymbolPicker` autocomplete + a
  popular-tickers chip row; Help page widened from 820 → 960px.
- ✅ **Docker build hang fix** — `BUILDX_NO_DEFAULT_ATTESTATIONS=1`
  documented in compose comments after the buildx provenance step
  hung during a rebuild.

**Open follow-ups from this week:**

- [x] Spec P2: LLM rationale prompt with horizon context (3 horizon-
  specific sentences per symbol per TRADEPRO-SPEC-001 §7) ✅ 2026-05-09
- [x] Help-page strategy visualisations (SMA crossover, RSI bands,
  MACD histogram, Donchian channel, 52w range, return distribution —
  visual learners). 6 inline recharts demos in Indicators + Risk
  metrics topics. ✅ 2026-05-09
- [x] Help-page **Data Sources** topic listing every external feed
  with status, cost, what it provides ✅ 2026-05-09
- [x] Health page **external-source status** card (Yahoo / Finnhub /
  Ollama / T212 with last-success age and degraded indicator)
  ✅ 2026-05-09 — `/health/integrations` endpoint + Data sources
  panel on the Health page
- [ ] **Historical P/E snapshot store** to replace basket-relative as
  the long-term valuation lens (spec §10 Q1)
- [ ] **SEC EDGAR** integration — free 10-K/10-Q filings, would feed
  the snapshot store and the rationale layer
- [ ] **Insider trades + recommendation trends** — yfinance +
  Finnhub both expose these and we don't currently use them
- [ ] **Portfolio simulation engine** — Monte Carlo + stress + DCA on
  live portfolio. New Phase B added below. To be discussed.
- [x] Research + Backtest "Run all 5 strategies" UX — replaces
  per-strategy selection friction with a single fan-out + consensus
  view. Symbol picker autocomplete on both pages. ✅ 2026-05-09

---

## Where we are now (April 2026)

Concrete, working today on `main`:

**Research stack (Python, runs on Mac M-series):**
- Five rule-based strategies: buy-and-hold, SMA crossover, RSI mean
  reversion, MACD signal cross, Donchian breakout.
- Comparator (`tradepro-compare`) that runs `N strategies × M symbols`,
  outputs a JSON payload with backtest stats (CAGR, Sharpe, max-DD),
  per-regime stress evidence (13 historical windows: dot-com, GFC,
  COVID, 2022 rate shock, etc.), per-symbol market-state verdict
  (BUY / HOLD / WAIT / AVOID) with a transparent decision trace, a
  per-symbol Wall Street consensus snapshot from Yahoo, and macro
  context (VIX, 10Y, S&P drawdown, active stress regimes).
- Five ETF watchlists pre-defined: `etf_uk_core`, `etf_us_core`,
  `etf_us_sector`, `etf_factor`, `etf_all` (35-symbol union).
- Local Parquet cache, idempotent refresh, manifest + JSONL event log
  per run.

**API (.NET 8):**
- Read endpoints (Firebase-auth): `/api/marketdata/*`,
  `/api/signals/*`, `/api/simulations/*`, `/api/watchlists/*`,
  `/api/compare/{universes, latest}`.
- Ingest endpoint (static-token auth): `/api/ingest/{compare,
  backtest, scan, model_prediction}`.
- Pluggable provider abstraction (Yahoo / Stooq / Binance) with
  Yahoo as the only one currently advertised.

**Frontend (React + Vite):**
- Dashboard, Scanner, Signal detail (with hit-rate card), Simulations,
  Charts, Help, **Compare ETFs** (the headline page).
- Compare page: bucket triage (BUY today / WAIT / AVOID), strategy
  matrix (rows = ETFs, columns = strategies, cells = LONG/flat),
  expand panel with full decision trace, all-strategies stats, regime
  evidence, and Wall Street cross-check.

**Dev infra:**
- `docker compose up` brings up the API and frontend with hot reload.
- `tradepro-push` ships JSON from the Mac to the API over a static
  bearer token.

**Deploy:**
- Frontend → Firebase Hosting on push to `main`.
- API → Azure App Service on push to `main`.
- Strategies stay on the Mac.

---

## Key assumptions

These are the constraints every design decision sits on top of. Argue
with one, the plan changes.

| # | Assumption | Implication if it changes |
|---|---|---|
| A1 | **Single user.** No multi-tenancy. Auth is "is it me, or someone I let in?" — a static ingest token + a Firebase UID whitelist. | Multi-tenant SaaS would need user IDs everywhere, per-user data partitioning, billing, and rate limiting. |
| A2 | **UK-resident retail investor; ETF-led, individual stocks layered on later.** Defaults: GBP, LSE `.L` symbols, UK 0.5% stamp duty on buys. ETFs are the lead use-case ("which ETF should I hold for years?"). Individual stocks (FTSE 100, US mega-caps) are in the watchlists today but get a richer treatment in Phase 5b — fundamental ratios + earnings narrative — once ETF execution is solid. | A US-resident default would change fee model, watchlist, and tax-wrapper modelling. Going stocks-first instead of ETF-first changes which signals matter most (P/E + earnings vs Sharpe + drawdown). |
| A3 | **Daily bars today, real-time feed planned.** Currently EOD only — strategies fire on close-to-close events. Phase 7 introduces an optional intraday/real-time feed (Alpaca, IBKR, Polygon) for the symbols a user actively holds, gated per-watchlist so we don't burn quota on the whole universe. | When real-time lands: data layer adds streaming source(s), a new "live" tab on Compare, and bucket assignments refresh on tick rather than once per day. |
| A4 | **The Mac is the source of truth for compute.** Heavy work (backtests, model training) runs locally on the M-series. The API only stores + serves the JSON the Mac pushes. | Moving compute to the cloud means provisioned infra costs, GPU/CPU plans, and probably AWS Lambda or a managed batch service. |
| A5 | **Yahoo Finance is primary, best-effort.** Free, no key, but rate-limited and subject to upstream changes. Failures are tolerated (return empty rather than crash). | If Yahoo gets blocked or sunsets the unofficial API, we'd need Alpha Vantage / Finnhub / IBKR with API keys + paid tiers. |
| A6 | **Rule-based strategies, not ML — yet.** Every verdict is explainable: a human can read the rules in `market_state.py:_classify`. | Adding ML means model-versioning, training pipelines, drift monitoring, and a different transparency story (feature importance instead of an `if`-ladder). |
| A7 | **Recommendations are decision aids, not advice.** No regulated advice claim. The UI says so explicitly. | Regulated advice means FCA / SEC compliance, custody questions, and is out of scope. |
| A8 | **Backtests use `adj_close` (total return).** Dividends and splits are baked in. Cross-currency rankings (e.g. `etf_all`) are valid for Sharpe / CAGR % / max-DD % because those are currency-neutral; absolute fee accounting is per broker. | Mixing fees naively across currencies would distort return ladders. |
| A9 | **Free-tier infra by default.** Firebase Hosting Spark + Firestore Spark + Azure App Service F1 covers single-user load at £0/mo. F1 sleeps after ~20m idle. | Going public or sharing the URL needs a B1+ App Service or a CDN/Cloudflare front. |
| A10 | **AWS migration is planned but undated.** Today's deploy targets Azure App Service. Code is portable (env-driven URLs, no Azure SDKs). | When the migration happens, only `azure-api-deploy.yml` + `appsettings.json` env keys need to change. |
| A11 | **ETFs aren't analyst-rated.** Yahoo returns null `recommendationKey` for baskets. The Wall Street cross-check is informational for stocks; for ETFs it shows "not rated" and doesn't degrade the BUY decision. | If a future provider rates ETFs, we'd surface that rating in the cross-check. |
| A12 | **Manual `tradepro-push` for now.** No auto-refresh; data freshness is whatever the operator last ran. | Phase 4 introduces scheduled `launchd` jobs so the page is always ≤24h stale. |

---

## Roadmap

### Phase A — Production Azure deployment (PARKED, low effort)

Local stack is the daily driver — everything works end-to-end on
`docker compose up -d`. The deployed Azure + Firebase URLs exist
and CI is wired (`azure-api-deploy.yml`, `firebase-hosting-deploy.yml`)
but three config gaps prevent feature parity with local:

1. **Ingest token** — Azure App Service → Configuration → Application
   settings → add `Ingest__Token` = a long random secret
   (`openssl rand -hex 32`). Mirror in `~/.tradepro/credentials.prod`
   so a Mac-side `tradepro-compare --push` against the prod URL
   actually populates the deployed Compare page. Without this the
   public site shows an empty cache forever.

2. **Trading 212 keys (optional)** — same Azure config page:
   `Trading212__Mode=demo`, `Trading212__ApiKey`, `Trading212__ApiSecret`.
   When set the deployed Portfolio tab + T212 endpoints light up;
   skipped, they return the same `enabled: false` envelope the local
   stack does.

3. **Finnhub key (optional)** — `Finnhub__ApiKey` from finnhub.io free
   tier. Enables forward-earnings warnings on the deployed daily
   digest. No-op until set.

After all three are in place, the deployed UI mirrors local feature-
for-feature. Until then: local-only is the supported path; the public
site renders a stale or empty cache. Reactivate when the local flow
has a stable user pattern worth sharing.

Estimated effort: 30 minutes of Azure Portal clicking + one
`tradepro-compare --push` against the prod URL to populate the cache.
No code changes required — env-var driven.

### Phase D — Data persistence + AWS infra (foundation for everything below)

**The gap:** today everything lives in volatile shapes:
  - Compare payloads → JSON files in `/data/compare` (lost when the
    api container is wiped without the named volume).
  - Fundamentals → fetched fresh on every comparator run; nothing
    historical is preserved.
  - Sentiment scores → ephemeral; same headlines re-scored each cycle.
  - Backtest results → never persisted; user can't compare today's
    backtest to last week's.

  Several upcoming phases (snapshot store for P/E-vs-history per
  spec §10 Q1, portfolio simulation Monte Carlo per Phase B, gem
  hunter recovery tracking per Phase G) all depend on **historical
  series we don't currently retain**. We need a real data layer.

**The proposal:** stand up AWS infra reusing the existing
EnergyCosmos account so we don't need a fresh signup or billing
setup. Three components, layered cheap → expensive:

  **D1 — S3 archive of compare payloads (1 day)**
  - Every successful `tradepro-compare --push` also uploads the
    payload to `s3://tradepro-archive/compare/<universe>/<run_id>.json`
  - Versioned objects + lifecycle policy: keep 90d hot, transition
    to Glacier IR for older
  - Cost: pennies/month at our volume. Unblocks "show me how the
    NVDA verdict changed week-over-week" + audit trail for the
    decision-trace verifier.

  **D2 — Postgres (RDS) for the snapshot store (3 days)**
  - Tables: `fundamentals_snapshots` (symbol, fetched_at, P/E,
    expense_ratio, n_holdings, dividend_yield, …), `analyst_consensus_snapshots`,
    `sentiment_scores` (headline_id, scored_at, sentiment, themes)
  - Cron job from the worker writes a snapshot row each refresh
  - Unblocks the historical-P/E-vs-own-5yr-median lens the spec
    actually wants (currently using basket-relative as a stand-in)
  - RDS db.t4g.micro is ~£10/mo; pause when the engine isn't
    running to halve that

  **D3 — Compute migration: worker → Lambda + EventBridge (3 days)**
  - Replace the docker worker with a Lambda triggered by EventBridge
    every 30 min. Same Python codebase, packaged as a layer
  - Removes the "did you remember to restart the worker container"
    failure mode that plagued the May 2026 sessions
  - Macbook strategy run stays as a local-dev convenience (single
    command, no cloud dependency)

**Why now:** Phase B (portfolio simulation) needs daily price
history retained. Phase G (gem hunter) needs week-over-week
recovery signal tracking. Phase R (risk rating) doesn't strictly
need persistence but benefits from drawdown history. D1 unblocks
all three; D2 unblocks the snapshot store the spec actually needs;
D3 fixes the worker reliability gap once and for all.

**Migration path:** D1 first (zero-risk, pure archive). D2 second
(adds a real query engine without removing anything). D3 last
(only after D1 + D2 are solid). Local docker stack stays as the
dev experience throughout — AWS is the **persistence + scheduled
compute** plane, not a replacement for the local feedback loop.

**Estimated total: 7 days.** Not blocking Phase B (Phase B can run
on existing in-memory data); blocks the historical-P/E lens that
the spec currently has as a parked item.

---

### Phase G — Gem Hunter (deep-value mean-reversion scanner)

**The gap:** TradePro's current BUY signals favour instruments
already in an uptrend (above SMA200, positive 12m momentum). That's
correct for trend-following but blind to **the asymmetric upside
of a quality name beaten down to a real entry**. A FTSE 100 stock
−40% from its 5y peak with intact fundamentals + early RSI recovery
is the canonical "gem" — and today the engine doesn't surface it
because Family-1 strategies all read "below SMA200, weak momentum"
as a sell signal.

**The proposal:** new `find_gems()` scanner that runs after the
existing comparator and identifies instruments matching a separate
profile:

  Required (all of):
  - **Quality intact** — 5y Sharpe ≥ 0.5, max-DD recovery
    historically ≤ 24mo (i.e. it has come back from drawdowns
    before, not a permanent value trap)
  - **Down meaningfully from 5y peak** — drawdown_from_peak_pct
    ≤ −25% (not a 5% pullback, a real correction)
  - **In bottom quartile of 52w range** — range_position_pct ≤ 25
  - **Positive cross-basket valuation flag** — CHEAP per the new
    P/E hybrid lens (P/E for stocks, yield for ETFs)

  At least one of (recovery signal):
  - **RSI bouncing** — RSI ≥ 35 AND rising over last 5 bars
    (mean-reversion entry)
  - **Above SMA200 just turned** — crossed above in last 20 bars
  - **Cross-basket momentum z-score improving** — first positive
    z after a string of negative

  Filters out:
  - Penny stocks / micro-cap (market cap floor)
  - Single-stock concentration risk (passive horizon n_holdings
    handles this for ETFs)
  - Names with sentiment ≤ −0.30 (something's actually broken)

**Output:** new universe `gems_today` with 5-15 candidates per
refresh. Surfaced on the Decide page as a dedicated "Gems" tab
alongside the existing BUY/WAIT/AVOID buckets. Each gem carries
the same horizon classification as everything else — typically
swing WATCH (oversold but not yet confirmed) + long-term BUY
+ passive BUY for ETFs.

**Why it matters for the user's stated goal:** "Find a real Gem" —
the ETF universes already cover S&P 500 / FTSE 100 / sector +
factor ETFs. Gem hunter gives the user the **contrarian** lens on
the same data.

**Estimated effort:** 1 day for the scanner + universe wiring,
half a day for the dashboard tab + email digest section, half a
day for behave scenarios + tooltip / help-content explainer.
Total: 2 days.

---

### Phase R — Risk rating per recommendation

**The gap:** every BUY / WAIT / AVOID + horizon classification +
swing composite tells the user **what to do**. None of them tell
the user **how risky doing it is**. A BUY on USMV (low-volatility
factor ETF) and a BUY on TSLA (40%+ annualised vol, 50%+ drawdown
history) both render as "BUY" in the email — but the position size
the reader should take is wildly different.

**The proposal:** every row gets a `risk_rating` field — LOW /
MEDIUM / HIGH / EXTREME — derived from a transparent rule chain
visible in the decision trace:

  Risk inputs (all sourced from existing fields):
  - **Annualised volatility** (`vol_30d_annual_pct`) — primary
    driver. <15% = LOW, 15-25% = MEDIUM, 25-40% = HIGH, >40% =
    EXTREME.
  - **Max-DD recovery time** — historically slow recoverer
    (>3y) bumps the rating one tier.
  - **Sentiment material-negatives** — ≥3 in last 7d bumps one tier.
  - **Range position** — at 52w highs (≥80th pctile) bumps one tier
    (mean-reversion risk).
  - **Cross-basket dispersion** — symbol's z-score volatility
    relative to peers; outlier names get a tier bump.

  Cap: rating can move at most ±2 tiers from the volatility
  baseline so a tame ETF doesn't end up EXTREME from sentiment alone.

**Surfaced as:**
  - Dashboard: a small pill next to each bucket label (LOW = green,
    MEDIUM = amber, HIGH = red, EXTREME = magenta)
  - Email digest: column added to the BUY/WAIT/AVOID tables
  - PDF: explicit "Risk: HIGH — annualised vol 38%, ≥3y recovery
    historically" line on every per-symbol page
  - MCP: `risk_rating` and `risk_factors` fields on every row so
    Claude can quote them in conversation

**Position-sizing recommendation (optional, Phase R+):** combine
risk rating with portfolio size to suggest a per-position cap:
LOW ≤ 25%, MEDIUM ≤ 15%, HIGH ≤ 8%, EXTREME ≤ 4%. Stays advisory,
honest about being heuristic.

**Why it matters:** the swing composite already mixes quality +
valuation + event + price. Risk rating is **orthogonal** — it
asks "if this BUY is wrong, how bad is the downside?" That's the
question the user has been asking implicitly when they ask "should
I really hold VUKE if it might go to 36p?"

**Estimated effort:** half a day for the rule chain + per-row
attachment, half a day for UI surfacing (dashboard pill + email
column), half a day for behave scenarios + help-content. Total:
1.5 days.

---

### Phase B — Portfolio simulation engine (NEXT MAJOR after Phase X)

**The gap:** TradePro tells you what to BUY / WATCH / AVOID right now,
across three horizons. But it doesn't yet answer **"if I keep this
composition (or actioned all the BUY_MORE / TRIM advice), what's my
likely range of outcomes over 1y / 3y / 5y?"** — and that's the
question every retail allocator actually has to answer before
deploying capital.

This phase adds a `simulate_portfolio()` engine that takes the live
T212 portfolio (or a hypothetical one) and produces an outcome
distribution using established techniques, layered cheap → expensive:

**Layer 1 — Monte Carlo bootstrap (cheap, lands first)**
- Resample N=5000 paths from each holding's historical daily returns
  (block-bootstrap, preserves autocorrelation + fat tails)
- Output: 5th / 50th / 95th percentile equity curves over 1y/3y/5y
- Maximum drawdown distribution; probability of −10/−20/−30% DD
- Sequence-of-returns risk: order of bad years vs good years matters
  more than mean return on accumulation/retirement glide paths

**Layer 2 — Stress backtest (cheap, lands together)**
- Apply 2008 GFC, 2020 COVID, 2022 rate-shock daily returns to the
  CURRENT portfolio weights
- "If we re-ran the GFC starting today, what's your equity drawdown?"
- Compare to the bucket-vote AVOID rate during those windows — does
  the engine catch the bad regime in time?

**Layer 3 — DCA / contribution modelling**
- £X/month contributions over the horizon
- Two policies: DCA into current weights vs DCA into engine-advised
  weights (BUY_MORE → ↑ allocation, TRIM → ↓, HOLD → maintain)
- Cost-basis tracking + tax-lot estimate so the simulator answers
  "how much do I have to put in to hit £Y by 2031?"

**Layer 4 — Mean-variance / risk-parity optimisation (heavier)**
- Markowitz efficient frontier: alternative weight vectors for the
  same target return at lower variance, or higher return at same
  variance — bounded by T212 tradeable instruments
- Risk parity: equal-risk-contribution sizing as a sanity-check baseline
- Black-Litterman extension if we want to inject the engine's views
  (BUY = positive view, AVOID = negative)
- CVaR / Expected Shortfall as the tail-risk metric, not just std-dev

**Layer 5 — Engine-advised portfolio comparison (the headline output)**
- Side-by-side: "your current" vs "engine-advised" vs "S&P 500 / global
  index baseline"
- Same Monte Carlo machinery on each, plot all three on one chart
- Honest framing: this is hypothetical. Past returns ≠ future. Make
  the assumption banner LOUD on the simulation page.

**Open design questions for discussion:**
1. Returns source — yfinance daily returns per holding, or block-
   bootstrap from a regime-aware index? Affects how realistic tail
   events look.
2. Cross-asset correlation — IID resampling underestimates joint
   drawdowns. Need a copula or a "sample whole-day-row from history"
   approach to keep correlations alive.
3. Currency handling — VUKE.L (GBP) and VUSA.L (USD) holdings need
   FX-adjusted returns; do we treat as one ccy at horizon end, or
   surface both?
4. Interface — new Simulations tab "Portfolio Monte Carlo" alongside
   the existing per-symbol backtest, or fold into the Portfolio page
   as a "Project forward" panel?
5. Compute placement — runs in the worker container (Python, has
   numpy / pandas) vs frontend (recharts can render, but 5000 paths
   in JS is wasteful)? Worker-side, push the percentile bands to API.

**Why now:** the horizon classification engine just shipped, so for
the first time the system has **three distinct holding-period
verdicts per symbol**. Simulating a portfolio across those horizons
turns the engine's advice into a concrete probability statement — a
massive uplift over "trust me, BUY this".

**Estimated effort:** 1–2 weeks for layers 1–3 (the headline
deliverable), 2–3 weeks more for layer 4. Layer 5 is presentation,
mostly UI work.

**Memory anchor:** new project memory needed. Tag: portfolio-sim.

---

### Phase X — Multi-family signal stack + swing-trading composite (NEXT MAJOR)

Diagnosis (2026-05-04): every existing strategy (`sma_crossover`,
`macd_signal_cross`, `rsi_mean_reversion`, `donchian_breakout`,
`buy_and_hold`) is a price-vs-its-own-moving-average indicator.
Same family. They agree most of the time, so the bucket vote can't
distinguish "broadly strong basket" from "this one outperforming
peers". To get genuine alpha we need uncorrelated signal families.

| Family | What it asks | Status |
|---|---|---|
| 1. Price / technical | Is price above its own MA? | ✅ 5 strategies + range-position guard (May 2026) |
| 2. Valuation | Is this cheap? | ✅ basket-relative P/E (stocks) + yield (ETFs) hybrid lens. Historical-P/E vs own median still needs snapshot store. |
| 3. Cross-sectional / factor | How does this rank vs peers? | ✅ rank + zscore annotation per row |
| 4. Event-driven | Recent earnings beat + retreat? | ✅ `BEAT_AND_RETREAT` signal + Finnhub forward calendar (May 2026) |
| 5. Macro overlay | What regime are we in? | ⚠️ partial via etf_macro_proxies |
| 6. Sentiment | What's the news saying? | ✅ two-tier demotion: BUY→WAIT at -0.30, any→AVOID at -0.45 |

**Build order:**
1. ✅ Cross-sectional momentum rank (annotation, not yet a verdict driver)
2. ✅ Valuation flag — hybrid P/E (stocks) + yield (ETFs); historical
   median vs own 5y still parked pending snapshot store
3. ✅ Earnings calendar via Finnhub free tier + recent-beat-and-retreat detection
4. ✅ `evaluate_swing(symbol)` composite scorer 0–8 across
   quality / valuation / event / price layers; STRONG_BUY at ≥6
5. ✅ **Horizon Classification Engine** (TRADEPRO-SPEC-001 §6) — splits
   the verdict into swing / long-term / passive horizons so the same
   instrument gets independently-scored advice per holding period
6. Tranche-based position sizing: T1=40% now, T2=30% on RSI ≤ 35,
   T3=30% on RSI ≤ 30; max 20% per name; cash sleeve for reserves
7. Exit rules: profit target T1×1.20 (sell 40%), stop T1×0.85 (full),
   trailing 12% off local high once price > T1×1.10

**Why park the big composite:** building `evaluate_swing` right
needs position state (which tranches deployed, cost basis,
days-since-beat per symbol). That's the Phase 2 portfolio-aware
engine territory; ship together as one cohesive feature once T212
positions sync is wired into the rationale layer.

Memory anchors: `project_phase3_multifamily_signals.md`,
`project_phase2_portfolio_aware.md`.

### ✅ Phase 0–3: research + verdict pipeline (DONE)

The platform now answers "today, should I BUY / WAIT / AVOID, and
which ETF" with backtest evidence, regime survival, decision trace,
and analyst cross-check.

### Phase 4.5 — Data model formalisation + UI-editable config

The compare payload has grown organically (rows, regimes, market_state,
fundamentals, news, sentiment, currency, errors, llm). Time to make it
a first-class contract.

- [ ] **Versioned schema**: every payload carries `schema_version`
      (semver). Bumps when fields are removed or semantics change;
      compatible additions don't bump.
- [ ] **Pydantic / dataclass models** in `tradepro_strategies/schema.py`
      replace ad-hoc dicts. The comparator + ingest endpoint validate
      against the same schema. Renders as JSON Schema for free.
- [ ] **Generated TypeScript types**: `npm run gen-types` reads the
      Python schema and writes `frontend/src/api/types.generated.ts`.
      Eliminates the manual TS↔Python drift that already cost us one
      missing-import build break.
- [ ] **Storage shape mirrors API shape**: `FileCompareStore` writes
      the same envelope it returns over HTTP — already true, but lock
      it in by serialising via the schema, not by hand.

### Phase 7 (extended) — UI-editable configuration

The user has called out that thresholds, watchlists, regimes, and fee
preferences should be tunable without a code change. Today they're
all in code. This phase moves them server-side and exposes a Settings
page.

- [ ] **Watchlists**: editable from the UI, persisted in Firestore
      (or the file-backed store as a simpler bridge). Replaces the
      hard-coded `WATCHLISTS` dict.
- [ ] **Sentiment thresholds**: `SENTIMENT_DEMOTION_THRESHOLD` and
      `SENTIMENT_MIN_MATERIAL` editable from the UI. Each compare run
      reads the active values; the payload's `llm.demotion_rule`
      already exposes them so the change is visible.
- [ ] **Regime windows**: add custom regimes from the UI ("Brexit
      vote 2016-06-23 → 2016-07-08").
- [ ] **Fee model**: per-broker presets (Trading212, Freetrade, IBKR
      tiers) editable, default applied by region.
- [ ] **LLM provider/model**: choose via UI (drop-down of installed
      Ollama models + 'use Anthropic API' toggle with key entered in
      Settings).
- [ ] **Custom watchlists from the UI** *(user-stated 2026-05-03)*:
      "create watchlist like any other trading app". Settings page
      gets a Watchlists section with: list existing universes
      (pre-built + user-created), create with a name + symbol
      autocomplete, edit / delete. Server-side store sits next to
      compare cache (`<Compare:StorePath>/watchlists/<name>.json`).
      Mac comparator picks them up by name from the same `--watchlist`
      flag — no code change to add a new universe.
- [ ] **Symbol autocomplete** *(user-stated 2026-05-03)*: any free-
      text symbol input (Signals, Watchlist editor) gets a dropdown
      backed by `/api/marketdata/search` → Yahoo's symbol search
      endpoint. Filters by ticker + company name; preview shows
      currency / venue. Stops the "I typed NV instead of NVDA"
      class of error; today that returns a 500 (now hardened to a
      graceful 'no data' but autocomplete prevents the trip
      entirely).

### Phase 4 — Robust ETF execution (IN PROGRESS)

The output is correct. The plumbing isn't yet trustworthy enough for
a daily decision tool.

- [x] **Persistence**: `FileCompareStore` reads/writes
      `<Compare:StorePath>/<universe>.json` (default `/data/compare`
      in containers, named-volume in compose). Atomic writes
      (tmp + rename + fsync). Hydrates from disk on startup.
      Survives restarts and deploys.
- [x] **Scheduled refresh**: `strategies/scripts/refresh.sh` +
      `com.tradepro.refresh.plist` + `install-launchd.sh`. One-shot
      install runs every ETF universe daily at 22:30 UTC, logs to
      `~/.tradepro/logs/refresh-<date>.log`, exits non-zero on any
      failure.
- [x] **Stale-data banner**: traffic light on `/compare` —
      green ●&nbsp;Live <24h, amber ●&nbsp;Stale 24-72h with the exact
      `tradepro-compare` command shown, red ●&nbsp;Very stale >72h
      with a "refresh before deciding" warning.
- [x] **Per-symbol fetch error reporting**: comparator emits a
      top-level `errors` array (symbols that failed to fetch or
      came back empty); each row carries `data_age_days` so a stale
      price gets a visible 'Nd stale' badge in the matrix instead
      of silently feeding the rule chain.
- [x] **Currency awareness**: each row carries the venue-derived
      currency (`.L`→GBP, `.DE`→EUR, default USD). The payload
      flags mixed-currency runs (`currency_mix.is_mixed`); the UI
      shows a per-row Ccy column + a warning above the matrix
      when the universe spans more than one currency.
- [x] **Health/details endpoint + page**: `/health/details`
      returns API status, Mac heartbeat liveness, and per-universe
      cache freshness in one payload. New /health page polls every
      30s and shows a single 'is the system OK?' verdict (ok /
      warn / needs_attention) with three cards underneath.
- [ ] **Run history page**: list past comparator runs with
      `run_id`, timestamp, universe, row count, strategies, status.
      Click a `run_id` → view the full event log (the JSONL
      emitted by `RunLogger`) and the manifest. **Deferred** —
      requires keeping multiple runs per universe in the store
      (currently latest-only).
- [ ] **Per-decision audit trail**: from a Compare row, click "why
      this verdict" → land on a page with every input + every rule
      that ran, with a permalink stable across re-runs. **Deferred**
      — depends on run history.
- [ ] **Correlation-ID logs**: structured backend logs threading a
      correlation ID per ingest request through every log line.
      **Deferred** — invisible to users without log access; lower
      priority than the visible Phase 4 items above.
- [ ] **Verbose run logging on the Mac side**: every comparator run
      should emit an event JSONL with per-symbol fetch latency,
      LLM-call latency + cache hit/miss, sentiment-scoring totals,
      and failure reasons — already partial, formalise into the
      `RunLogger` so the run history page can render a full
      timeline. Pairs with traceability above.

### Phase 5a — ETF fundamentals + market news (DONE part 1)

- [x] **ETF fundamentals** from Yahoo's quote summary: dividend
      yield, expense ratio, AUM, top-10 holdings with weights,
      sector mix, inception date. Bond ETFs additionally surface
      yield-to-maturity + duration. Rendered in the Compare expand
      panel.
- [x] **Per-symbol news headlines** from Yahoo's news feed
      (`yfinance.Ticker.news`). Top 5 in the expand panel —
      clickable to source, publisher + relative timestamp. No
      sentiment scoring yet (Phase 6).
- [ ] **Manual news flag**: operator can mark a news item as
      "material" (e.g., earnings beat, guidance cut) so it
      influences the verdict. Belongs after Phase 6's automatic
      sentiment so manual flags only override the model when the
      operator disagrees with it — not the primary input.

### Phase 5c — Information sources expansion (live + historical news + uploads)

User-stated requirement (2026-05-02): the platform must factor in
live news, historical news, financial reports, and user-uploaded
documents into its decisions. Today's news pull is a snapshot from
Yahoo's per-symbol feed; this phase makes the information layer
first-class and extensible. **No-hallucination contract still
applies** — anything fed in here gets cited like any other source,
and an LLM rationale that references it must verify against it.

#### 5c-i — Live news + price feeds (real-time signal)

User-stated (2026-05-03): "got Trading212 application access. We can
get real live data which will be up-to-date then Yahoo." T212's
public API exposes portfolio + orders + instruments-search but NOT
streaming quotes — for live prices we still need a streaming
provider. Two-track plan:

- [x] **Trading212 config + status probe** *(2026-04-28)*: `Trading212Options`
      bound from config (`Mode=disabled|demo|live`, key/secret), typed
      `Trading212Client` with Basic auth + rate-limit headers, and
      `GET /api/integrations/trading212/status` to confirm a key pair
      reaches the broker. Off by default; live spec at
      `strategies/docs/api.json`. Important: T212 has **no OHLC** — Yahoo
      stays for prices.
- [ ] **Trading212 portfolio sync**: `/equity/positions`,
      `/equity/account/summary`. Read-only first. Used for paper-trading
      reconciliation in Phase 8 + 'highlight what you actually own'
      badge on the Compare page.
- [ ] **Trading212 instruments registry**: `/equity/metadata/instruments`
      gives the full T212 universe (tickers, currencies, venues, ISINs,
      tradeability flags). Becomes the autocomplete source for the
      symbol picker (Phase 7).
- [ ] **Live price stream** *(separate from T212)*: Alpaca free tier
      (US equities, IEX-only, 200 req/min) OR IBKR Gateway delayed
      quotes via `ib_insync`. WebSocket for active symbols, fallback
      to REST polling. Bucket assignments refresh on tick rather
      than once per day. **Opt-in per universe** so free-tier users
      pay nothing.
- [ ] **RSS / Atom ingestion**: per-watchlist feed registry. Defaults:
      Yahoo per-symbol news (already pulling), Reuters/AP/FT business
      RSS, sector-specific feeds (FT Markets, Bloomberg open RSS).
- [ ] **Polling worker**: Mac-side scheduled job (launchd) that
      checks each feed every N minutes; deduplicates by URL hash;
      writes new items to a local store keyed by (symbol, fetched_at).
- [ ] **Webhook receiver** *(optional, paid feeds)*: a small endpoint
      on the API that accepts pushed news items from a service like
      Polygon, Finnhub, or a custom Comet task. Same auth pattern as
      `/api/ingest/compare`.
- [ ] **Rate-of-arrival signal**: when a symbol's news rate spikes
      (e.g. 5× the 30-day average) the comparator surfaces a "news
      surge" flag on the row. Material event detector — surfaces
      *before* the LLM reads anything.

#### 5c-ii — Historical news archive

- [ ] **Cumulative store** of every fetched news item, keyed by
      (symbol, published_at, source). Survives restarts; queryable
      by date range. Lives at `~/.tradepro/news/<symbol>.parquet`
      (one Parquet per symbol, append-only, compressed).
- [ ] **Backfill from Yahoo `Ticker.news` history** + free tier
      providers (NewsAPI free tier, GDELT) — best-effort; gaps in
      coverage are fine and tagged as such.
- [ ] **Historical-context retrieval at decision time**: for a given
      verdict, surface "the last 5 BUY signals on this symbol came
      with these headlines" — connects the rationale to past
      precedents.

#### 5c-iii — Document upload (PDF / HTML / TXT)

The thing that makes the platform research-aware. User uploads a
prospectus, an analyst report, a press release; the system extracts
text, chunks, embeds, and retrieves at decision time.

- [x] **Mac-side extractor** (`tradepro_strategies.documents`):
      pdfplumber for PDFs (per-page sections), trafilatura for
      HTML (boilerplate-stripped), pass-through for TXT / MD.
      Returns ExtractedDocument with sha256 + char_count +
      structured sections.
- [x] **Ingest CLI**: `tradepro-doc-upload <file> --symbols QQQ,VOO
      --title "..."` extracts locally, builds a manifest, pushes
      via the existing `/api/ingest/document` token-auth endpoint.
      Raw files stay on the Mac — only structured text + manifest
      cross the wire.
- [x] **API document store + endpoints**: `FileDocumentStore`
      mirrors `FileCompareStore` (file-backed, atomic-rename,
      hydrates on startup). Read endpoints:
      `GET /api/documents` (list, optional ?symbol= filter),
      `GET /api/documents/{id}` (full envelope),
      `GET /api/documents/{id}/text` (extracted text — used by the
      Mac comparator at decision time).
- [x] **Behave coverage**: features/documents.feature verifies
      extraction shape, manifest building (uppercase symbols, uuid
      doc_id), and rejection of unsupported extensions.
- [ ] **Frontend `/documents` page**:
  - Drag-and-drop upload (forwards to /api/ingest/document via the
    Mac CLI initially; native browser upload in a follow-up that
    pipes raw bytes to the Mac for extraction)
  - List of uploaded docs with title, size, linked symbols, date
  - Click to view extracted text + which decisions it has been
    retrieved into
- [x] **Chunking**: section-aware sliding window
      (`tradepro_strategies/embeddings/chunker.py`). Default
      ~500-token windows / ~100-token overlap, never crosses
      section boundaries (PDF page = section). Stable
      `chunk_id = sha1(doc_id, section, chunk_idx, prefix)` so
      re-chunking is idempotent and embeddings only re-run when
      content actually changes.
- [x] **Embeddings**: `OllamaEmbedder` (default
      `mxbai-embed-large`, 1024-dim, ~700 MB). Override via
      `TRADEPRO_EMBED_MODEL`. Failures (model not pulled / Ollama
      down) surface visibly via `embed.failed` events, never
      crash the comparator.
- [x] **Vector store**: Parquet at
      `~/.tradepro/cache/embeddings.parquet`, brute-force cosine
      similarity in numpy. Plenty for single-user volume (dozens
      of docs, hundreds of chunks); upgrade to DuckDB-VSS or
      sqlite-vec when we cross ~10k chunks.
- [x] **Retrieval at decision time**: comparator calls
      `update_embeddings()` at run start (catches up new docs),
      then per-symbol queries the store for top-5 chunks with
      score ≥ 0. Chunks land in the rationale's
      `retrieved_evidence` block; the LLM treats them as allowed
      facts subject to the same verifier (claims unsupported by
      either structured row OR retrieved chunk text → template
      fallback).
- [x] **Citation URI scheme**: every chunk has
      `tradepro://documents/<doc_id>#chunk-<chunk_id>` — stable,
      cite-able, and surfaced on every Rationale.retrieved_chunks
      entry so the frontend can deep-link the source.

#### 5c-iv — Document discovery (automated)

- [ ] **Edgar (SEC) feed**: ingest 10-K, 10-Q, 8-K filings for
      tickers in any watchlist automatically. SEC's full-text
      search is free.
- [ ] **Earnings transcripts**: pull from Yahoo's earnings tab
      (free) where available; otherwise prompt user to upload.
- [ ] **Per-ETF prospectus auto-fetch**: for each ETF in a
      watchlist, follow the Yahoo `summaryProfile.fundFamily` →
      issuer URL → prospectus PDF link. One-off per ETF; doc is
      cached forever.

#### 5c-v — Verifier integration with retrieved evidence

When the rationale builder pulls in retrieved chunks as facts, the
verifier MUST be able to follow citations back to the chunk's text.
This means:
- [ ] Every chunk has a stable URI (`tradepro://documents/<doc_id>
      #chunk-<n>`) that the LLM cites.
- [ ] The verifier compares claims against the *concatenated* fact
      bundle (structured row data + retrieved text). If a citation
      doesn't trace, the rationale is rejected (template fallback
      kicks in, same contract as today).

### Phase 5b — Extending to individual stocks (gated on ETF being solid)

ETFs answer "which basket should I own". Individual stocks need a
different signal set: fundamental ratios + earnings narrative
matter, not just price action. This phase **starts only after Phase
4 + 5a leftovers are clean** for ETFs — we don't fork the focus
mid-ETF.

- [ ] **Fundamental ratios per stock** from `Ticker.info` /
      `.financials` / `.balance_sheet` / `.cashflow`: P/E,
      forward P/E, PEG, P/B, ROE, debt/equity, free-cash-flow
      yield, revenue growth (3y / 5y), gross / operating / net
      margins, margin trend. All free, daily refresh, deterministic.
- [ ] **Per-stock decision_trace checks**: "P/E vs sector median",
      "ROE > 15% (quality)", "FCF yield > 5% (cheap)",
      "Debt/equity < 1 (balance-sheet clean)". Plug into the
      existing `_classify` rule chain — same transparency contract.
- [ ] **Stock-specific Compare page** (or universe-aware mode on
      the existing one): show ratios alongside CAGR / Sharpe in
      the matrix. Top holdings is replaced by sector + competitor
      peers.
- [ ] **Stock watchlists**: keep the existing `uk_ftse100_sample`
      and `us_megacap_sample`; add curated thematic lists
      (defensives, dividend aristocrats, UK quality compounders).
- [ ] **Cross-asset universe**: an option to compare a target
      stock vs the ETF that holds it ("VOO vs MSFT") so a user
      can decide whether to pick the company or just buy the
      basket.

### Phase 6 — LLM-derived qualitative signals

**Most of Phase 6 applies to ETFs too — not just stocks.** Only
Phase 6b (earnings/10-K narrative analysis) is stock-only by
nature. Sentiment, macro/sector narrative, prospectus summaries,
rationale rewrite, and Q&A chat all apply directly to the ETF
flow and don't depend on Phase 5b shipping first.

| Sub-phase | ETFs | Stocks | Description |
|---|---|---|---|
| 6a — News sentiment | ✅ | ✅ | The Iran-war / tariff-shock demotion case |
| 6b — Earnings / 10-K analysis | ❌ | ✅ | Depends on Phase 5b |
| 6c — Decision rationale rewrite | ✅ | ✅ | Plain-English summary of `decision_trace` |
| 6d — Q&A chat | ✅ | ✅ | "Is GLD a good hedge?", "VWRP vs VUSA?" |
| 6e — Macro / sector narrative | ✅ | ✅ | Weekly 200-word digest per universe / sector |
| 6f — ETF prospectus summary | ✅ | — | Methodology, top weights, rebalance rules |
| 6g — Holdings-aware ETF news | ✅ | — | "What's affecting QQQ this week" via its top 5 |

Strict principle: **the LLM produces context and explanation, never
the verdict.** The decision stays rule-based and transparent;
otherwise we throw away every transparency win we've built. LLM
outputs go in as additional `decision_trace` checks (with their own
status), not as overrides.

#### Phase 6 architecture

- [ ] **`LlmProvider` abstraction** in a new `strategies/
      tradepro_strategies/llm/` module. Implementations:
  - `OllamaProvider` — local M-series MPS, free, private, default
    for high-frequency / low-stakes tasks.
  - `ClaudeProvider` — Anthropic API, used for high-value /
    nuanced tasks where 8B-parameter models fall short. ~£1-5/mo
    at single-user volume.
- [ ] Every LLM call has a strict JSON output schema; parse
      failures → `null` / "no signal" rather than crashing the
      run. Outputs are cached by `hash(prompt + input)` so we
      never re-score the same headline twice.
- [ ] Telemetry: tokens-in, tokens-out, latency, parse-success
      rate per task — exposed on `/api/health/llm` for the same
      observability story as the worker badge.

#### Phase 6a — News sentiment (lowest stakes, highest frequency)

- [ ] Local Ollama scores each headline already in the news
      pull: `{sentiment: -1..+1, themes: [string], material: bool}`.
- [ ] Aggregate to a **7-day rolling sentiment trend per symbol**.
      Add a new check to `decision_trace`:
      "Sentiment trend (7d)" → pass / warn / fail.
- [ ] **Bucket demotion rule**: BUY → WAIT when sentiment trend is
      sharply negative AND material headlines are present. The
      Iran-war / tariff-shock case lands here.
- [ ] **Bias guard**: never *promote* a verdict on positive
      sentiment alone — the rule-based check has to also pass.

#### Phase 6b — Earnings call + 10-K narrative analysis (stock-only)

Depends on Phase 5b. Likely uses Claude API (Anthropic) — the
analysis is more nuanced than 8B Ollama can reliably handle.

- [ ] Ingest earnings-call transcripts and 10-K MD&A / risk-factors
      sections. Source: SEC EDGAR (free), Yahoo earnings tab, or
      paid transcript provider later.
- [ ] LLM extracts a structured tag set:
      `{guidance_direction, management_emphasis: [string],
      new_risks: [string], segment_highlights: [string],
      tone: bullish|neutral|cautious}`.
- [ ] Render a "Fundamental narrative" panel on the stock
      detail page. Tags are surfaced as filter chips so a user
      can scan for "guidance cut" across the watchlist.

#### Phase 6c — Decision rationale rewrite

- [ ] Take the existing rule-based `decision_trace` and an LLM
      writes a 1-paragraph plain-English summary
      ("QQQ is BUY because price is healthily uptrending, RSI is
      not overbought, and 4 of 5 strategies are currently long.
      The tech-heavy tilt does mean it's the most exposed to a
      rate shock — see GFC -55%, 2022 -25% in the regime panel.").
- [ ] Cached aggressively — the rationale only re-generates when
      the rule outputs actually change.

#### Phase 6d — Q&A chat as an MCP server (Anthropic's Model Context Protocol)

User insight (2026-04-30): the Q&A use-case is **literally the MCP
server pattern** — "I ask a question, a server with tools and
resources runs simulations / fetches data / formats the answer via
an LLM". MCP standardises exactly this shape; using it gives us a
free integration with Claude Desktop, Cursor, and any future
MCP-aware client without a custom chat protocol.

- [ ] **`tradepro-mcp` server** — a small Python service using the
      official `mcp` SDK (or `fastmcp`). Exposes the platform as
      tools + resources + prompts:
  - **Resources** (read-only):
    - `tradepro://compare/{universe}` — latest ranked-comparison
      payload (the JSON we already store).
    - `tradepro://watchlists` — defined universes.
    - `tradepro://regimes` — the 13 historical stress windows.
    - `tradepro://settings` — current sentiment thresholds + rule
      configuration (Phase 7 / 4.5 territory).
  - **Tools** (the LLM can invoke):
    - `run_comparison(universe, strategies?)` — kick a fresh run
      on the Mac, push, return the new payload.
    - `get_market_state(symbol)` — price, RSI, SMA, drawdown,
      decision_trace.
    - `get_regime_history(symbol, strategy)` — per-regime stats.
    - `get_news_with_sentiment(symbol)` — news + LLM scores.
    - `get_health()` — system + worker status.
  - **Prompts** (one-click templates):
    - `analyse_etf(symbol)` — "Should I invest in {symbol}? Use
      compare data, regime history, and current news to answer."
    - `compare_etfs(symbols)` — side-by-side analysis.
- [ ] **Strict-decision contract** *(unchanged)*: the LLM in the
      MCP client (Claude Desktop or our own chat UI) is allowed
      to summarise + cite + explain. The actual BUY/SELL/HOLD
      verdict comes from the rule-based payload — the MCP tools
      *return* it, the LLM doesn't compute it. Citations make
      this auditable: every claim is traceable to a tool call.
- [ ] **Two consumers** out of the box:
  1. **Claude Desktop** integration — drop a config in `~/Library/
     Application Support/Claude/claude_desktop_config.json` and
     ask questions of your own portfolio.
  2. **In-app `/chat` page** — same MCP server, accessed via a
     thin frontend chat using either Claude API or local Ollama
     as the conversational LLM (the MCP server is provider-
     agnostic).
- [ ] Telemetry: every MCP tool invocation gets a structured event
      in the run log so the same observability story applies.

#### Phase 6e — Macro / sector narrative (ETF + stock)

- [ ] Weekly 200-word LLM-generated digest per ETF universe and
      per sector ("what's going on in tech / fixed income /
      emerging markets this week"). Inputs: macro context bar,
      regime overlap, recent news headlines, sector returns.
- [ ] Surfaced at the top of `/compare` as a context paragraph
      below the macro context bar — the qualitative companion to
      the quantitative VIX / 10Y / S&P-drawdown numbers.
- [ ] Refresh weekly; cached aggressively.

#### Phase 6f — ETF prospectus summary

- [ ] Pull the official prospectus (PDF or methodology page) for
      each ETF in the universe.
- [ ] LLM extracts: index it tracks, rebalance cadence, top
      country / sector / factor weights, replication method
      (physical vs synthetic), holdings concentration (% in top
      10), securities lending policy, ESG screens if any.
- [ ] Render in the expand panel as a one-paragraph "what's in
      this ETF" summary above the holdings list. Beats reading 80
      pages — a beginner can grok the structural exposure in 30
      seconds.
- [ ] Cached for the lifetime of the prospectus version (months).

#### Phase 6g — Holdings-aware ETF news synthesis

The bridge between ETF-level analysis and the stock-level news +
fundamentals pipeline (Phase 5b + 6b).

- [ ] For each ETF in a universe, take the news + sentiment of
      its top 5-10 holdings (already pulled in Phase 5/5b).
- [ ] LLM synthesises into a single "what's affecting QQQ this
      week" paragraph — weighted by holding weight, focused on
      material news.
- [ ] Surfaced in the expand panel as "Underlying news synthesis".
      An ETF investor sees the company-level signals without
      drilling into 10 different stocks.

### Phase 7 — Live signals + alerts (incl. optional real-time feed)

- [ ] Daily signal evaluation across all watchlists.
- [ ] Email / push (Firebase Cloud Messaging) when a row's verdict
      changes (e.g., BUY → WAIT).
- [ ] Per-user notification rules ("only alert me on BUY in
      `etf_uk_core`").
- [ ] **Real-time / intraday feed (opt-in)**. Cheapest credible
      providers:
  - Alpaca: free tier, 200 calls/min, IEX-only quotes (US equities).
  - IBKR Gateway: delayed (free with account) or real-time (per-
      exchange sub) via `ib_insync` from the Mac.
  - Polygon: starter tier ~$30/mo for end-of-day-with-snapshots.
  Strategy: keep daily bars as the universe-wide truth, layer a
  per-symbol streaming source on top for "live" mode on the symbols
  a user actually holds. Bucket assignments refresh on tick instead
  of once per day. Stays opt-in to keep free-tier costs at zero
  for users who don't need it.

### Phase 8 — Paper trading + journal

- [ ] In-app portfolio ledger (no broker integration yet).
- [ ] When the user clicks BUY on a card, log a paper trade with the
      exact `run_id` it came from — full traceability from decision
      back to the data that supported it.
- [ ] P&L roll-up per strategy and per regime.

### Phase 9 — Broker integration (read-only first)

- [ ] Read-only broker connection (Trading212 / Freetrade / IBKR).
- [ ] Reconcile real holdings against paper positions.
- [ ] Manual order placement with confirmation guardrails.
- [ ] Audit log of every order and the signal that led to it.

### Phase 10 — Production hardening

- [ ] Real OpenTelemetry → Azure Monitor (or AWS CloudWatch post-
      migration). Trace ID propagated from frontend → API → Python
      worker.
- [ ] AWS migration (see A10). Code is already portable.
- [ ] Cost dashboard: a small `/api/health/cost` endpoint that
      reports current usage vs free-tier limits.

---

## Non-goals (still — for now)

- Real money execution before Phase 9.
- HFT / sub-second strategies.
- Multi-tenant SaaS.
- Regulated investment advice (we're a decision aid, with the
  disclaimer in the UI).

---

## Cost stack (current — AWS personal account)

Migrated off Azure + Firestore in May 2026. The system now runs on a
single small EC2 host and an EBS volume; everything else is free-
tier or per-call.

| Layer | Service | Notes |
|---|---|---|
| UI hosting | Firebase Hosting (Spark) | 10 GB bandwidth / mo |
| API + worker host | EC2 t4g.small (eu-west-2) | ~£3-4/mo with auto-stop overnight, ~£1.30/mo idle on EBS |
| Auth | Firebase Auth (Spark) | free for single user |
| Secrets | AWS Secrets Manager (`tradepro/all`) | £0.30/mo per secret |
| State store today | In-memory + JSON files on EBS | wiped by every redeploy — see Phase 5 below |
| Heavy compute | The M-series Mac | electricity only |

**Phase 5 — Postgres migration** (planned, not yet shipped): replace
in-memory stores (`IPendingOrdersStore`, `IPaperSnapshotStore`,
`IPaperBacktestStore`) and JSON-file stores (`FileCompareStore`,
`FileSettingsStore`, `FileDocumentStore`, `InMemoryWatchlistStore`)
with Postgres on the same EC2 host (or RDS db.t4g.micro at ~£10/mo).
Required for prod because every redeploy currently wipes pending
orders, snapshots, settings, and watchlists. Tracked in
[ARCHITECTURE.md](docs/ARCHITECTURE.md) §Stores.

---

## UK-specific defaults

- Currency GBP; UK 0.5% stamp duty on LSE main-market shares (AIM /
  ETFs exempt — `--stamp-duty 0`).
- Yahoo `.L` suffix (`BARC.L`, `LLOY.L`); `^FTSE` / `^FTMC` indices.
- Tax wrapper (Phase 8): ISA mode = no CGT on sell side, £20k/yr
  contribution cap.
- Brokers (Phase 9): Trading212 / Freetrade / Hargreaves Lansdown.

---

## Push pipeline

```
 ┌───────────────┐   POST /api/ingest/<kind>   ┌────────────────┐
 │  Mac (Python) │ ──────────────────────────▶ │ Azure / AWS    │
 │  scheduled    │   Authorization: Bearer     │ .NET 8 API     │
 │  launchd      │                             └───────┬────────┘
 └───────────────┘                                     │ GET /api/compare/...
                                                       ▼
                                             ┌──────────────────┐
                                             │ Firebase Hosting │
                                             │ React UI         │
                                             └──────────────────┘
```

- Auth: ingest token (single static value) on `/api/ingest/*`,
  Firebase ID token on everything else.
- Secrets bundle: **AWS Secrets Manager** at `tradepro/all` in
  eu-north-1 (JSON key/value blob). Both the Mac engine and the EC2
  API read it via SDK at boot. Env vars + appsettings still override
  for local dev. The Mac fallback `~/.tradepro/credentials` is kept
  as a tertiary path for backwards compatibility but new secrets go
  to SM, not to that file.
