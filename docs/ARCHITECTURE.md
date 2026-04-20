# TradePro — solution architecture

Living doc. Update whenever we add a component. Key rule: if a newcomer would
be surprised, it goes here.

---

## 1. What TradePro is (30-second version)

A personal trading-strategy platform that answers two questions:

1. **Right now:** Given a defined list of stocks and a trading strategy, what
   is worth buying or selling today? (The Scanner view.)
2. **Retrospectively:** If I had followed this strategy over the last N years,
   how much money would I have made / lost? What were my worst drawdowns?
   (The Simulations view.)

It's a decision-aid, not advice. No real orders are placed. Everything is
traceable — every number on a page can be drilled back to the inputs and
data that produced it.

---

## 2. Why this stack

| Layer | Service | Why |
|---|---|---|
| Web UI | React + Vite, hosted on Firebase Hosting (project `smsp-291e3`) | Free tier covers single-user + small audiences; good DX; custom domain `showsoldprice.com` |
| Auth | Firebase Authentication (Google sign-in) | Zero-code identity, JWT tokens, UID whitelist via backend config |
| API | .NET 8 minimal API on Azure App Service (Linux, `tradepro-api`) | Strongly-typed domain; good with numeric code; deploys as a zip |
| DB | Firestore on Firebase Spark (free) | Realtime listeners give us bidirectional Mac↔UI without opening ports on the Mac |
| Research & heavy compute | Python (`tradepro_strategies`) on a MacBook M4 | Vectorised pandas/numpy is fast; MPS for future ML; free |
| Data | Yahoo Finance only today (Stooq + Binance coded but disabled — Stooq now needs an API key, Binance is crypto-only). IBKR / Alpha Vantage planned. | Free, no key |

Cost target: **£0/month** for a single user. F1 sleep and free-tier quotas
are our constraints, not money.

---

## 3. Components and how they talk

```
                       ┌──────────────────────────┐
                       │  Browser (React)         │
                       │  showsoldprice.com       │
                       └───┬──────────────┬───────┘
                           │              │
                  HTTPS +  │              │  Firestore realtime
                  bearer   │              │  (jobs, runs, watchlists)
                  token    │              │
                           ▼              ▼
                ┌────────────────┐   ┌──────────────────┐
                │  Azure App     │   │  Firestore       │
                │  Service       │   │  (smsp-291e3)    │
                │  (tradepro-api)│   └────────┬─────────┘
                │   .NET 8       │            │ realtime listener
                └───┬────────────┘            │
                    │ outbound                ▼
                    │           ┌─────────────────────────┐
                    │           │  Mac (M4) — Python      │
                    │           │    tradepro-worker      │
                    │           │    tradepro-refresh     │
                    │           │    tradepro-backtest    │
                    │           │    Parquet cache        │
                    │           │    Artefacts + JSONL    │
                    │           └───────────┬─────────────┘
                    │                       │ outbound only
                    ▼                       ▼
          ┌──────────────────────────────────────────┐
          │  Market data providers (free tier)       │
          │  Yahoo · Stooq · Binance · (IBKR later)  │
          └──────────────────────────────────────────┘
```

Key property: **the website never opens a connection into the Mac**. All
traffic is outbound from the Mac, or mediated via Firestore.

---

## 4. Data flow — "what's worth buying today?" (live scan)

1. User signs in with Google → Firebase issues an ID token.
2. Frontend calls `POST /api/signals/scan { watchlist, strategy, params }` with
   `Authorization: Bearer <token>`.
3. API validates token, checks UID is in `Firebase__AllowedUserIds`.
4. API fans out to 4 parallel calls to the chosen provider (e.g. Yahoo) for
   each symbol in the watchlist.
5. For each symbol: compute SMAs / RSI / trend, decide BUY / SELL / HOLD with
   a confidence score.
6. Return the grouped + ranked result to the UI.

Latency: ~5–10 s for a 10-symbol UK watchlist (dominated by Yahoo fetch).

---

## 5. Data flow — "deep backtest on my Mac" (async job)

This is the Firestore-based queue pattern. Use it when a scan is too
expensive for the API (F1 has 60 CPU-min/day).

1. User fills in backtest inputs (symbol, strategy, dates, capital).
2. Frontend writes a doc into Firestore:
   ```
   jobs/{autoId} = {
     kind: "backtest",
     status: "pending",
     user_uid: <their UID>,
     created_at: serverTimestamp,
     request: { symbol, strategy, from, to, params, fees, ... }
   }
   ```
3. **Mac `tradepro-worker`** has a realtime listener on
   `jobs where status == "pending"`. It sees the new doc within ~1 s.
4. Worker flips `status: "running"`, runs the backtest using cached data
   (hits provider only if cache is stale), writes local artefacts.
5. Worker writes `status: "complete"` with `stats` and a `manifest`
   pointing at the artefact dir on the Mac.
6. Frontend's listener on the same doc sees the status change and renders
   the result live.

If the Mac is asleep/off, the job sits in Firestore. When the Mac wakes and
the worker reconnects, pending jobs process in order.

---

## 6. Indicators explained (plain English)

### Simple Moving Average (SMA)

The average of the last N closing prices. SMA(20) means "average of the
last 20 daily closes".

- If today's SMA(20) = £3.40 and yesterday's was £3.35, short-term momentum
  is positive.
- SMA smooths noise so you see the trend, not the daily flapping.
- Lag: because it's an average, it always reacts late — roughly half the
  window size. SMA(200) lags by ~100 trading days.
- Useful as a trend proxy: price above its long SMA = uptrend; below =
  downtrend.

### SMA crossover — our first strategy

Track a **fast** SMA (e.g. SMA 20) and a **slow** SMA (e.g. SMA 50).

- **Golden cross** — fast crosses above slow ⇒ **Buy** signal. Interpretation:
  short-term momentum has turned up enough to drag the fast average above
  the long-term baseline.
- **Death cross** — fast crosses below slow ⇒ **Sell** signal.
- Works: in trending markets it catches the move early enough to ride it.
- Fails: in sideways markets it whipsaws — fast line keeps criss-crossing
  slow line, generating trade-after-trade with small wins and large cumulative
  fees.
- Knobs: `fast`, `slow`. Common presets: 5/20 (aggressive), 20/50 (classic),
  50/200 (long-term trend follower).

### Exponential Moving Average (EMA)

Same idea as SMA but recent prices count more than old prices. Reacts faster
but is also noisier. Use when lag is biting you.

### RSI (Relative Strength Index)

0–100 score measuring the ratio of recent gains to recent losses over 14
days.

- **Above 70**: "overbought" — price has climbed a lot, a pullback is
  statistically more likely. Bearish bias.
- **Below 30**: "oversold" — sharp fall, bounce is statistically more likely.
  Bullish bias.
- Between 30 and 70: no signal. Neutral zone.
- Works: mean-reverting stocks in range-bound markets.
- Fails: strong trends — a stock can stay above 70 for weeks while rising.

### MACD (not yet wired, but planned)

Momentum indicator built from two EMAs (12 and 26) plus a "signal line"
(EMA 9 of the difference). Buy when the MACD line crosses above the signal
line, sell when below. Same DNA as SMA crossover but with exponential
smoothing.

### Buy & Hold (baseline)

Buy on day 1, sell on day N. The benchmark every other strategy has to beat
*net of fees*. If your fancy strategy can't beat buy-and-hold on the FTSE
100 after UK stamp duty, it isn't working.

---

## 7. Strategies we have today

Registered in `strategies/tradepro_strategies/strategies/__init__.py` as
`REGISTRY`. Add a new one:

1. Create `strategies/tradepro_strategies/strategies/<name>.py` exporting
   a function `(prices_df, **params) -> signals_series`.
2. Add an entry to `REGISTRY`.
3. Done — CLI, worker, and backend endpoints all pick it up automatically.

Current:

| Name | Description | Params | Status |
|---|---|---|---|
| `buy_and_hold` | Long on day 1, flat at end | — | ✅ reference |
| `sma_crossover` | Golden/death cross of two SMAs | `fast`, `slow` | ✅ |
| `rsi_mean_reversion` | Buy on recovery from oversold, sell on cool-off from overbought | `period`, `low`, `high` | ✅ |
| `donchian_breakout` | Buy N-day high, exit on N-day low | `lookback` | planned |
| `macd_signal_cross` | Buy MACD > signal, sell below | `fast`, `slow`, `signal` | planned |

---

## 8. UK fee model (the default)

Every backtest/simulation applies a `FeeModel` to make the result honest:

- **Stamp duty**: 0.5% of notional on **buys** of LSE main-market shares.
  AIM shares and ETFs are exempt — override with `stamp_duty_rate=0`.
- **Commission per trade**: flat fee, configurable (Trading212/Freetrade =
  £0, HL ≈ £11.95).
- **FX spread**: when buying non-GBP assets from a GBP account. 0 by
  default.

If you change the fee model, note it in the run manifest — the number on
the equity curve is only comparable when fees are equal.

---

## 9. Observability (traceability you can rely on)

Every run on the Mac writes three things, keyed by a UUID `run_id`:

1. **JSONL event log**:
   `~/.tradepro/logs/YYYY-MM-DD/<run_id>.jsonl`
   One JSON object per line: timestamps, event names, key metrics. Grep-able.

2. **Artefact dir**:
   `~/.tradepro/artefacts/<run_id>/`
   - `manifest.json` — inputs + stats + git SHA of the strategies package.
   - `equity_curve.parquet` — the daily equity series.
   - `trades.parquet` — every buy/sell with price, size, fees.

3. **Firestore doc** (for jobs triggered via the UI):
   `jobs/{jobId}` and `runs/{run_id}` with summary stats + `manifest` blob.

Given a number on a chart, you can:
- Find the `run_id` in the Firestore doc or artefact dir.
- Open `manifest.json` to see exact inputs + git SHA.
- Check out that SHA, re-run with the same inputs, get the same number.

---

## 10. Parquet + DuckDB cache on the Mac

`~/.tradepro/cache/<provider>/<interval>/<symbol>.parquet` plus a
`<symbol>.meta.json` sidecar.

Why:
- Yahoo + Stooq are rate-limited. Caching prevents re-downloading the same
  15 years of daily bars on every backtest.
- Keeps the server path (F1 60 CPU-min) cheap.
- DuckDB can query across all parquet files (`SELECT * FROM 'cache/*/*.parquet'`)
  for universe-wide analytics.

Refresh cadence: `tradepro-refresh --watchlist uk --years 10` nightly via
`launchd`. Idempotent — merges by timestamp, so re-runs top up today's bar.

---

## 11. Security posture

- **API is UID-whitelisted.** Only Firebase users in
  `Firebase__AllowedUserIds` pass authorization. Empty list == any
  signed-in user (only for initial testing).
- **Firestore rules** deny everything by default. Users can only
  create/read their own `jobs/{id}`. The worker uses the Admin SDK server-
  side, which bypasses rules — its credentials live **only on the Mac** at
  `~/.tradepro/firebase-sa.json` (chmod 600).
- **Firebase web config is not a secret** — it ships in the bundle. Lock
  it down by adding HTTP-referrer restrictions in Google Cloud Console.
- **Never commit**: service-account JSONs, Azure publish profiles, Firebase
  Admin keys, `.env.local`.

---

## 12. Deployment surface

| Target | Trigger | Workflow |
|---|---|---|
| Firebase Hosting (live) | push to `main` touching `frontend/**` | `.github/workflows/firebase-hosting-deploy.yml` |
| Firebase Hosting (preview) | open/update PR touching `frontend/**` | `.github/workflows/firebase-hosting-preview.yml` |
| Azure App Service | push to `main` touching `backend/**` | `.github/workflows/azure-api-deploy.yml` |
| Firestore rules | manual `firebase deploy --only firestore:rules` from Mac | (no workflow yet) |

Secrets in GitHub repo:
- `FIREBASE_SERVICE_ACCOUNT_SMSP_291E3` — deploy Firebase Hosting.
- `AZURE_WEBAPP_PUBLISH_PROFILE` — deploy .NET API.

Secrets on Mac:
- `~/.tradepro/firebase-sa.json` — Firebase Admin SDK (worker uses this).
- `~/.tradepro/credentials` — optional API token for `tradepro-push`.

---

## 13. Roadmap to V1

See `ROADMAP.md` for the phased plan. Short version:

- **Phase 1 (now)** — Local cache, worker, UI job queue. We're here.
- **Phase 2** — Add RSI, MACD, Donchian strategies; walk-forward
  evaluation; watchlist CRUD.
- **Phase 3** — Live signal subscriptions (email/push when a stock in your
  watchlist fires a BUY).
- **Phase 4** — ML layer on top of the deterministic baselines.
- **Phase 5** — Read-only broker integration (IBKR, then Trading212/
  Freetrade if they expose APIs).

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **CAGR** | Compound annual growth rate. Your return expressed as an annualised %. |
| **Max drawdown** | Worst peak-to-trough fall in equity during the backtest. Tells you the emotional cost of following the strategy. |
| **Sharpe ratio** | Return per unit of volatility (annualised). Higher = smoother ride. > 1 is good for a long-only equity strategy. |
| **Walk-forward** | Fit parameters on years 1–5, test on year 6; slide window; repeat. Lies least about out-of-sample performance. |
| **Whipsaw** | A strategy that keeps flipping between buy and sell in choppy markets, losing money to fees each flip. |
| **Stamp duty** | UK-specific 0.5% tax on buying most LSE main-market shares. |
| **LSE** | London Stock Exchange. Symbols use the `.L` suffix on Yahoo (e.g. `BARC.L`). |
| **FTSE 100 / 250** | The 100 (and next 250) largest UK-listed companies by market cap. Yahoo symbols: `^FTSE`, `^FTMC`. |
