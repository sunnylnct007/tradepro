# TradePro Roadmap

Phased plan. Each phase is independently useful — ship, then extend.

## Phase 0 — Skeleton (this commit)

- Monorepo: `backend/`, `frontend/`, `strategies/`.
- .NET 8 minimal API with `IMarketDataProvider` abstraction.
- Three free providers wired up: Yahoo Finance, Stooq, Binance (crypto).
- React dashboard that lists candles from any provider.
- Python package with an SMA-crossover backtest as a reference strategy.

## Phase 1 — Data layer

- [ ] Add Alpha Vantage + Finnhub providers (API keys, free tiers).
- [ ] Add a caching layer (SQLite via EF Core for dev, Postgres on Azure).
- [ ] Daily scheduled ingestion job (Azure Function or hosted BackgroundService).
- [ ] Normalize candle schema across providers (OHLCV + adjusted close).
- [ ] Symbol universe management (watchlists stored server-side).

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
