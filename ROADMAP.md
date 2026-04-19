# TradePro Roadmap

Phased plan. Each phase is independently useful — ship, then extend.

## Phase 0 — Skeleton (this commit)

- Monorepo: `backend/`, `frontend/`, `strategies/`.
- .NET 8 minimal API with `IMarketDataProvider` abstraction.
- Three free providers wired up: Yahoo Finance, Stooq, Binance (crypto).
- React dashboard that lists candles from any provider.
- Python package with an SMA-crossover backtest as a reference strategy.

## Phase 1 — Data layer

- [ ] Local Parquet + DuckDB cache on the Mac (one write per symbol per day).
- [ ] `IbkrProvider` (Python only, via `ib_insync` against IB Gateway). IBKR
      delayed data is free with an account; real-time is a per-exchange sub.
      The Mac runs the gateway, the API never talks to IBKR directly.
- [ ] Add Alpha Vantage + Finnhub providers (API keys, free tiers) for the
      server-side API fallback path.
- [ ] Firestore as the server-side store (watchlists, scan history, backtest
      summaries). Free-tier friendly.
- [ ] Daily scheduled Mac job (`launchd`) that refreshes the cache, runs
      scans, and pushes results to Firestore.
- [ ] Normalize candle schema across providers (OHLCV + adjusted close).
- [ ] Symbol universe management (watchlists stored in Firestore).

## Phase 2 — Backtesting engine

- [ ] Port SMA/EMA/RSI/MACD indicators to C# and expose a `/backtest` endpoint.
- [ ] Standard metrics: CAGR, Sharpe, Sortino, max drawdown, win rate.
- [ ] Walk-forward + out-of-sample split.
- [ ] Persist backtest runs so the frontend can diff two runs.

## Phase 3 — Live signals

- [ ] Scheduled strategy evaluation on the watchlist.
- [ ] Signal history + alerts (email / push via Firebase Cloud Messaging).
- [ ] Paper-trading portfolio ledger (no real broker yet).

## Phase 4 — Models

- [ ] Python research notebooks → reproducible training pipeline.
- [ ] Classical ML first (gradient boosting on engineered features).
- [ ] Serve model predictions via a Python FastAPI sidecar called by the .NET API.
- [ ] Run locally on the M4 with MPS; optionally containerize for Azure.

## Phase 5 — Broker integration

- [ ] Read-only broker connection (Interactive Brokers / Alpaca / Zerodha Kite
      depending on geography).
- [ ] Manual order placement from the UI with confirmation guardrails.
- [ ] Audit log of every order and signal that led to it.

## Phase 6 — Production hardening

- [ ] Auth (Firebase Auth, single-user to start).
- [ ] Rate-limited public API tier (if `showmesoldprice` becomes public).
- [ ] Observability: OpenTelemetry → Azure Monitor.
- [ ] Cost dashboard — know what each run costs before you scale.

## Cost-effective starter stack (£0 / month)

Default path until usage justifies paid tiers:

| Layer | Service | Free-tier limit | Cost if exceeded |
|---|---|---|---|
| UI hosting | Firebase Hosting (Spark) | 10 GB bandwidth / mo | pay-as-you-go |
| Database | Firestore (Spark) | 1 GiB stored, 50k reads + 20k writes / day | pennies per 100k ops |
| API compute | Azure App Service **F1** | 60 CPU-min / day, sleeps when idle | £0 (goes to sleep instead) |
| Heavy compute | The M4 MacBook | — | electricity |

Single-user load is orders of magnitude below every free-tier limit, so
realistic monthly bill is £0. The two real constraints:
- App Service F1 sleeps after ~20 min idle → first request after a cold
  period takes 5–10s. Fine for a personal tool.
- F1 has no custom-domain SSL. Hit it from `*.azurewebsites.net` or put
  Cloudflare in front if you need the `api.showmesoldprice.com` subdomain.

**Upgrade path when F1 bites:**
- Azure App Service B1 (~£10/mo): always-on, custom-domain SSL.
- Azure Functions Consumption plan: pay only for invocations (first 1M
  free/mo), cold starts but no sleep. Rewriting the minimal API as Functions
  is straightforward — each endpoint becomes one function.

## UK-specific considerations

- **Currency:** default is GBP; the fee model in both backtesters applies the
  0.5% UK stamp duty to buys on LSE main-market shares. AIM-listed shares are
  exempt — override `stampDutyRate` to `0` for them.
- **Symbols:** Yahoo uses `.L` suffix for LSE equities (`BARC.L`, `LLOY.L`);
  `^FTSE` / `^FTMC` for the FTSE 100 / 250 indices.
- **Tax wrapper:** when simulations drive real decisions, add an ISA mode that
  ignores CGT on sell-side and caps new contributions at the £20,000/year
  annual allowance.
- **Brokers:** Trading212, Freetrade, IG, and Hargreaves Lansdown are the
  obvious integration targets (read-only first — see Phase 5).

## Running heavy work locally (M4 MacBook)

- Python backtester is designed for vectorised runs over the full universe.
- For neural models, `torch` auto-detects MPS on Apple Silicon. Set
  `device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")`.
- Results are emitted as JSON (`scripts/run_backtest.py --out …`), which the
  .NET API can ingest and serve to the UI — no GPU hosting required.

## Data storage (layered, minimal)

The guiding rule: **the Mac is the source of truth for raw + computed data;
the server only stores what the UI needs to show.**

- **Local cache (Mac):** Parquet files under `strategies/.cache/` keyed by
  `(provider, symbol, interval)`. A DuckDB file (`strategies/.cache/tradepro.duckdb`)
  indexes them so ad-hoc SQL queries over the whole universe are fast. This
  removes the per-run hit on free providers and makes backtests reproducible.
- **Server DB (Firestore — free tier):** Firestore on the Spark plan covers
  the whole single-user use case at £0/month. Free tier is 1 GiB stored,
  50k reads / 20k writes per day — ~100× more than we need. Documents stored:
  - `watchlists/{name}` — user-defined lists (replaces the in-memory preset).
  - `scans/{yyyy-mm-dd_strategy}` — every scan's BUY/SELL/HOLD buckets.
  - `backtests/{runId}` — input config + summary stats (equity curve stays as
    blob storage or kept local if it gets large).
  - `journal/{tradeId}` — user notes / decisions attached to signals.
  Firestore has a client-side SDK so the React app can subscribe and update
  in real-time — no extra API calls needed for read-heavy views. Only
  move to Postgres if you ever outgrow the daily read/write quotas.
- **Flow:** Mac runs backtests / model training → writes a result JSON →
  `POST /api/backtests/ingest` stores the summary → UI reads it back. Raw
  candles stay on the Mac; the API re-fetches live prices on demand.

### How the website talks to the Mac (answer: it doesn't)

Never expose the laptop to the internet. Instead the Mac pushes:

```
 ┌───────────────┐   HTTPS + bearer token   ┌────────────────┐    ┌──────────────┐
 │  Mac (Python) │ ───────────────────────▶ │ Azure API      │ ──▶│ Azure DB /   │
 │  scheduled    │   POST /api/ingest/...   │ .NET 8         │    │ blob storage │
 │  cron/launchd │                          └───────┬────────┘    └──────────────┘
 └───────────────┘                                  │
                                                    ▼
                                          ┌──────────────────┐
                                          │ Firebase Hosting │ (reads from API)
                                          │ React UI         │
                                          └──────────────────┘
```

- Endpoints (Phase 1): `POST /api/ingest/scan`, `POST /api/ingest/backtest`,
  `POST /api/ingest/model-prediction`. All require a shared secret header.
- Secret is stored on the Mac in `~/.tradepro/credentials` (chmod 600) and
  on Azure as an App Service config value.
- Scheduling on the Mac: `launchd` plist invoking `strategies/scripts/push.py`.
  Cron works too but `launchd` survives reboots cleanly.
- If a push fails, retry with exponential backoff and keep the last N JSON
  payloads on disk so nothing is lost.

## Perplexity Comet hook (optional)

Comet can be scheduled to do targeted research (earnings recaps, news
sentiment, macro context) and POST the output to a TradePro enrichment
endpoint. Wire-up pattern:
1. Expose `POST /api/enrichments` on the backend (Phase 1).
2. Configure a Comet task to hit that endpoint on a schedule with a JSON
   payload keyed by symbol.
3. The backtester / signal engine reads enrichments as an additional feature.

## Non-goals (for now)

- Real money execution before Phase 5.
- HFT / sub-second strategies — the data layer is EOD + intraday at best.
- Multi-tenant SaaS — this is a personal platform first.
