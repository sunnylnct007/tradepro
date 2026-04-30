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

### ✅ Phase 0–3: research + verdict pipeline (DONE)

The platform now answers "today, should I BUY / WAIT / AVOID, and
which ETF" with backtest evidence, regime survival, decision trace,
and analyst cross-check.

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
- [ ] **Per-symbol fetch error reporting**: instead of dropping
      symbols silently when Yahoo fails, surface them in the UI as
      "data unavailable — last known: 2026-04-25".
- [ ] **Currency awareness**: when `etf_all` mixes UK + US, label
      each row's currency in the matrix and warn against absolute-
      fee comparisons across currencies.
- [ ] **Traceability + observability** *(your stated priority)*:
  - Run history page: list past comparator runs with `run_id`,
    timestamp, universe, row count, strategies, status.
  - Click a `run_id` → view the full event log (the JSONL emitted
    by `RunLogger`) and the manifest (inputs + stats).
  - Per-decision audit trail: from a Compare row, click "why this
    verdict" → land on a page that shows every input + every rule
    that ran, with a permalink stable across re-runs.
  - Structured backend logs with correlation ID per ingest request.
  - Health probe + freshness probe exposed on `/health/details`.

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

#### Phase 6d — Q&A chat (last, lowest priority)

- [ ] Free-form chat ("should I be worried about TLT?") backed by
      a tool-using LLM that has read access to the platform's data
      (compare cache, regimes, fundamentals, news).
- [ ] **Hard rule**: the chat NEVER outputs a buy/sell decision.
      It explains, summarises, points the user at the relevant
      panels. The decision stays in the rule-based flow.

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
