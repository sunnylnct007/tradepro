# TradePro

A personal trading-strategy platform. The core question it answers is:

> **Given a defined list of stocks and a strategy, what's worth buying or
> selling today — and how much money would that strategy have made?**

- **Scanner** — run a strategy over a watchlist, get ranked BUY / SELL / HOLD.
- **Signal detail** — drill into a single symbol: reasons, indicators (SMA, RSI,
  52-week range), suggested stop/target.
- **Simulations** — backtest the same strategy on historical data, see the
  equity curve, P&L, CAGR, drawdown and Sharpe. UK fee model (0.5% stamp duty)
  is on by default.

## Stack

- **Frontend** — React + Vite + TypeScript, deployed to Firebase Hosting
  (`smsp-291e3` / `showmesoldprice`).
- **Backend** — .NET 8 minimal API, deployable to Azure App Service. Pluggable
  data providers: **Yahoo Finance, Stooq, Binance** (all free, no keys).
- **Research** — Python package (`strategies/`) with the same backtest
  semantics as the API, tuned for heavy local runs on an Apple-silicon Mac.
- **Domain** — `showmesoldprice.*` fronts the web app.
- **Region** — UK-first. GBP default, LSE `.L` symbols, stamp duty built in.

## Repo layout

```
backend/         .NET 8 Web API
  TradePro.Api/
frontend/        React + Vite + TS (Firebase Hosting)
strategies/      Python research / backtest package
.github/workflows/
  firebase-hosting-deploy.yml   prod deploy on push to main
  firebase-hosting-preview.yml  preview channel on PRs
  azure-api-deploy.yml          .NET API deploy on push to main
ROADMAP.md       phased build plan
```

## Quick start (local)

### Backend
```bash
cd backend/TradePro.Api
dotnet run
# Try:
# -> http://localhost:5080/health
# -> http://localhost:5080/api/marketdata/providers
# -> http://localhost:5080/api/watchlists/uk
# -> POST /api/signals/scan     { "watchlist": "uk", "strategy": "sma_crossover" }
# -> POST /api/signals/evaluate { "symbol": "BARC.L", "strategy": "sma_crossover", "lookbackDays": 365 }
# -> POST /api/simulations/run  (see the Simulations page in the UI)
```

### Frontend
```bash
cd frontend
cp .env.example .env.local
# set VITE_API_BASE_URL=http://localhost:5080 for local dev
npm install
npm run dev   # -> http://localhost:5173
```

### Strategies (Python, M-series friendly)
```bash
cd strategies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_backtest.py --symbol BARC.L --strategy sma_crossover \
    --from 2019-01-01 --capital 10000
```

## Deploying

### Frontend → Firebase Hosting (`smsp-291e3`)

1. In Firebase Console → Project settings → Service accounts, generate a new
   private key (or run `firebase init hosting:github` locally and let it
   provision the secret for you).
2. In GitHub → repo → Settings → Secrets → Actions, add a secret named
   **`FIREBASE_SERVICE_ACCOUNT_SMSP_291E3`** with the JSON contents.
3. Push to `main` — the `firebase-hosting-deploy.yml` workflow builds and
   deploys the `frontend/` app to the `live` channel. PRs deploy to a
   preview channel that auto-expires in 7 days.

Firebase web config (`apiKey`, etc.) is in `frontend/.env.production` — it's
not secret (it ships in the client bundle), but lock it down in Google Cloud
Console with HTTP-referrer restrictions once the custom domain is live.

### Backend → Azure App Service

1. Create an App Service (Linux, .NET 8) — suggested name `tradepro-api`.
   Edit `AZURE_WEBAPP_NAME` in `.github/workflows/azure-api-deploy.yml` if
   you pick a different name.
2. In the App Service overview, click **Get publish profile** to download an
   XML file.
3. In GitHub → Settings → Secrets → Actions, add a secret named
   **`AZURE_WEBAPP_PUBLISH_PROFILE`** with the XML contents.
4. In App Service → Configuration, set:
   - `Cors__AllowedOrigins__0` = `https://smsp-291e3.web.app`
   - `Cors__AllowedOrigins__1` = `https://showmesoldprice.com`
5. Push to `main` — the workflow builds, publishes, and deploys.

Point `VITE_API_BASE_URL` in `frontend/.env.production` at the App Service URL
(or your custom API subdomain, e.g. `https://api.showmesoldprice.com`).

## Design principles

- **Data is the foundation.** Providers are pluggable; no symbol, list or
  strategy is hardcoded into business logic. Adding a provider = one class
  + one DI line.
- **Defined universes, not the whole market.** You pick the watchlist — the
  scanner ranks *within* it. Start with `uk`, add your own.
- **Backtest and live signals share the same code paths.** Whatever the
  scanner recommends today is what the simulator would trade in backtests.
- **Reproducible research.** Python runs locally on the M4 and emits JSON the
  API can ingest; backtest results are deterministic given config + data.
- **Honest about uncertainty.** Confidence is displayed, not hidden. No
  claims of alpha that haven't been measured.

See [ROADMAP.md](./ROADMAP.md) for the phased plan.
