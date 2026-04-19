# TradePro

A personal trading research platform: ingest live + historical market data, run
backtests and simulations, and turn the output into trading decisions.

- **Frontend** — React + Vite + TypeScript, hosted on Firebase.
- **Backend** — .NET 8 minimal API. Pluggable data providers (Yahoo, Stooq,
  Binance out of the box; API keys optional). Hosted on Azure App Service.
- **Strategies / Research** — Python package (`strategies/`) for backtesting
  and model work. Runs locally on your M4 MacBook or in CI.
- **Domain** — `showmesoldprice.*` can front the web app (Firebase Hosting
  custom domain).
- **Region** — UK-first. Default currency GBP, LSE symbols, and UK stamp duty
  built into the simulator fee model (see ROADMAP for details).

## Repo layout

```
backend/         .NET 8 Web API
  TradePro.Api/
frontend/        React + Vite + TS (Firebase Hosting target)
strategies/      Python research / backtest package
docs/            Architecture + research notes
```

## Quick start

### Backend
```bash
cd backend/TradePro.Api
dotnet run
# -> http://localhost:5080/health
# -> http://localhost:5080/api/marketdata/providers
# -> http://localhost:5080/api/marketdata/candles?symbol=BARC.L&provider=yahoo
# -> http://localhost:5080/api/watchlists/uk
# -> POST /api/simulations/run  (see Simulations page in the UI)
```

### Frontend
```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` in `frontend/.env.local` (copy from `.env.example`).

### Strategies (Python)
```bash
cd strategies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/run_backtest.py --symbol AAPL --strategy sma_crossover
```

## Design principles

- **No hardcoded symbols, strategies, or providers.** Everything is resolved
  by name from config or request parameters.
- **Providers are pluggable.** Adding a new data source = one class
  implementing `IMarketDataProvider` plus an entry in `appsettings.json`.
- **Research in Python, execution/API in .NET.** The API can shell out to
  Python for heavier models, or consume results via a shared data store.
- **Reproducible.** Backtests take a config (symbol, range, strategy, params)
  and return a deterministic result you can diff.

See [ROADMAP.md](./ROADMAP.md) for the phased build plan.
