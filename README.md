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

Works out of the box — no Firebase project or Azure subscription needed. Auth
is automatically bypassed in development (`ASPNETCORE_ENVIRONMENT=Development`).

### Backend (.NET 8)
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

### Frontend (React + Vite)
```bash
cd frontend
cp .env.example .env.local      # edit — only VITE_API_BASE_URL is required locally
npm install
npm run dev                     # -> http://localhost:5173
```

If `.env.local` has no `VITE_FIREBASE_*` values, the UI runs in "local mode"
(no sign-in button, no token on API calls) and the backend accepts it.

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

### Auth — Firebase ID tokens + UID whitelist

In production the API refuses anything without a valid Firebase ID token
issued by the `smsp-291e3` project, and it further restricts access to a
whitelist of Firebase user IDs (so only you can hit your API).

1. In Firebase Console → Authentication, enable the **Google** sign-in
   provider.
2. Sign into the deployed web app once with your Google account.
3. Grab your UID: Firebase Console → Authentication → Users → copy the UID
   for your account.
4. In the Azure App Service → Configuration, add an app setting:
   - `Firebase__AllowedUserIds__0` = `<your UID>`
   - (Add `…__1`, `…__2` for more users.)
5. The App Service should already have `Firebase__ProjectId=smsp-291e3` (it
   defaults to that via `appsettings.json`).

Leaving `Firebase__AllowedUserIds` empty means **any** signed-in Firebase user
on this project can call your API — so set it before going public.

### Backend → Azure App Service (`smspapi` in Canada Central)

1. App Service already exists: `smspapi`, URL
   `https://smspapi-cxhzera4excgckfw.canadacentral-01.azurewebsites.net`.
   (If you rename it, update `AZURE_WEBAPP_NAME` in
   `.github/workflows/azure-api-deploy.yml` to match.)
2. In App Service → Deployment Center, download the publish profile XML and
   add it as the GitHub secret **`AZURE_WEBAPP_PUBLISH_PROFILE`**.
3. In App Service → Configuration, set these app settings:
   - `ASPNETCORE_ENVIRONMENT` = `Production`
   - `Firebase__ProjectId` = `smsp-291e3` (already in appsettings.json; only set
     here if you want to override).
   - `Firebase__AllowedUserIds__0` = `<your Firebase UID>` (grab it from
     Firebase Console → Authentication → Users after signing in once).
   - `Cors__AllowedOrigins__0` = `https://smsp-291e3.web.app`
   - `Cors__AllowedOrigins__1` = `https://showmesoldprice.com`
4. Push to `main` → the workflow builds, publishes, and deploys.

`VITE_API_BASE_URL` in `frontend/.env.production` already points at the
Azure URL. Swap it for a custom domain (e.g. `https://api.showmesoldprice.com`)
once you add one.

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
