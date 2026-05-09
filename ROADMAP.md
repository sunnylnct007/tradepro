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

- [ ] Spec P2: LLM rationale prompt with horizon context (3 horizon-
  specific sentences per symbol per TRADEPRO-SPEC-001 §7)
- [ ] Help-page strategy visualisations (SMA crossover, RSI bands,
  MACD histogram, Donchian channel, 52w range — visual learners)
- [ ] Help-page **Data Sources** topic listing every external feed
  with status, cost, what it provides
- [ ] Health page **external-source status** card (Yahoo / Finnhub /
  Ollama / T212 with last-success age and degraded indicator)
- [ ] **Historical P/E snapshot store** to replace basket-relative as
  the long-term valuation lens (spec §10 Q1)
- [ ] **SEC EDGAR** integration — free 10-K/10-Q filings, would feed
  the snapshot store and the rationale layer
- [ ] **Insider trades + recommendation trends** — yfinance +
  Finnhub both expose these and we don't currently use them

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

## Cost-effective stack (£0 / month)

Default path until single-user usage justifies paid tiers:

| Layer | Service | Free-tier limit |
|---|---|---|
| UI hosting | Firebase Hosting (Spark) | 10 GB bandwidth / mo |
| Database | Firestore (Spark) | 1 GiB stored, 50k reads + 20k writes / day |
| API compute | Azure App Service **F1** | 60 CPU-min / day, sleeps when idle |
| Heavy compute | The M-series Mac | electricity only |

**Upgrade triggers:**
- F1 sleep is annoying for active dev → B1 (~£10/mo): always-on,
  custom-domain SSL.
- Firestore quotas hit (unlikely single-user) → Postgres on
  CockroachDB Serverless or Neon.

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
- Secret on the Mac: `~/.tradepro/credentials` (chmod 600).
- Server: `Ingest__Token` env var, set in Azure App Service config
  (will move to AWS Secrets Manager post-migration).
- Failure mode: `tradepro-push` retries with exponential backoff;
  payloads are kept on disk under `~/.tradepro/artefacts/<run_id>/`
  so nothing is lost.
