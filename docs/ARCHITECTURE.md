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

### MACD (Moving Average Convergence Divergence)

Momentum indicator built from two EMAs (default 12 and 26).
- **MACD line** = fast EMA − slow EMA. Above zero = uptrend, below = downtrend.
- **Signal line** = 9-day EMA of the MACD line — a smoothed version.
- **Histogram** = MACD line − signal line. Tells you if momentum is
  accelerating (rising bars) or decelerating (falling bars).

Buy when the MACD line crosses above the signal line (momentum turning
positive); sell on the reverse. Same DNA as SMA crossover but EMAs react
faster than SMAs, so signals come earlier — at the cost of more whipsaw.

### Donchian channel breakout

Made famous by the Turtle Traders. Two lines:
- Upper Donchian = highest close of the last N bars (default 20).
- Lower Donchian = lowest close of the last N bars.

Buy when today's close pushes **above** the prior upper line — a true
new high, the market is breaking out. Sell when it drops **below** the
prior lower line. Catches strong sustained trends, sits flat when the
market is range-bound. Loves momentum, hates mean reversion.

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
| `macd_signal_cross` | Buy MACD > signal, sell below (EMA-based momentum) | `fast`, `slow`, `signal` | ✅ |
| `donchian_breakout` | Buy on close above prior N-day high, sell on close below prior N-day low | `lookback` | ✅ |

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

Anything that looks like an acronym in the UI should be defined here. Add new
ones as we introduce them — this is the canonical reference.

### Indicators & maths

| Term | Meaning |
|---|---|
| **SMA** (Simple Moving Average) | Unweighted average of the last N closing prices. `SMA(20)` = average of last 20 daily closes. Smooths noise; always lags the price. |
| **EMA** (Exponential Moving Average) | Like SMA but recent prices count more than old ones. Reacts faster, noisier. `EMA(12)` is the standard "fast" EMA in MACD. |
| **WMA** (Weighted Moving Average) | Less common cousin of EMA; linear (not exponential) decay. Not used today. |
| **MACD** (Moving Average Convergence Divergence) | `EMA(12) − EMA(26)` = MACD line; `EMA(9)` of the MACD line = signal line. Momentum oscillator. |
| **RSI** (Relative Strength Index) | 0–100 score of recent gains vs losses over N bars (default 14). Above 70 = overbought, below 30 = oversold. |
| **Donchian channel** | Rolling high and low of the last N bars (close-based). Breakout strategy buys when today's close exceeds the prior N-bar high. |
| **ATR** (Average True Range) | Average daily price range — a measure of volatility. Not wired yet; planned for position sizing. |
| **Bollinger Bands** | SMA ± 2 × rolling standard deviation. Not wired yet. |

### Strategy jargon

| Term | Meaning |
|---|---|
| **Golden cross** | The fast moving average crossing *up through* the slow moving average. Bullish signal. |
| **Death cross** | The fast MA crossing *down through* the slow MA. Bearish signal. |
| **Crossover** | Generic term for one line crossing another (SMA/EMA/MACD). |
| **Breakout** | Price pushing above a previously established high (or below a low). Donchian is a breakout strategy. |
| **Mean reversion** | The assumption that prices return to an average — the opposite of trend-following. RSI mean-reversion uses this. |
| **Whipsaw** | A strategy flipping BUY→SELL→BUY in quick succession in a choppy market, losing to fees each flip. |
| **Long / short** | Long = bet on price going up (we own the stock); short = bet on it going down. Today we only go long. |
| **Flat** | No position. Neither long nor short. |

### Performance stats

| Term | Meaning |
|---|---|
| **CAGR** (Compound Annual Growth Rate) | Return expressed as an annualised %. Doubling over 10 years ≈ 7.2% CAGR. |
| **Total return** | Raw % gain from start to end, without annualising. |
| **Max drawdown** | Worst peak-to-trough fall in equity during the backtest. The emotional cost of following the strategy. |
| **Sharpe ratio** | `(mean daily return / stdev of daily return) × √252`. Return per unit of volatility, annualised. > 1 is good for a long-only strategy; > 2 is rare. |
| **Sortino ratio** | Like Sharpe but only penalises *downside* volatility. Less used, planned for Phase 2. |
| **Expectancy** | Average return per trade = `winRate × avgWinner + lossRate × avgLoser`. Positive = strategy pays net, regardless of win-rate. |
| **Win rate** | % of round-trip trades that finished profitable. |
| **Hit rate** | Synonym for win rate in this app. |
| **Walk-forward** | Fit parameters on years 1–5, test on year 6; slide; repeat. Least-biased measure of out-of-sample performance. Phase 2. |
| **Alpha** | Excess return over a benchmark (e.g. the FTSE 100). Not computed yet. |
| **Beta** | Sensitivity to the benchmark. Not computed yet. |

### Market / execution

| Term | Meaning |
|---|---|
| **LSE** | London Stock Exchange. Yahoo suffixes its symbols with `.L` (e.g. `BARC.L`). |
| **NYSE / NASDAQ** | New York Stock Exchange / NASDAQ. No suffix on Yahoo (e.g. `AAPL`). |
| **Ticker / Symbol** | Short code identifying a stock (e.g. `BARC.L` = Barclays on LSE). |
| **FTSE 100** | Top 100 UK-listed companies by market cap. Index ticker on Yahoo: `^FTSE`. |
| **FTSE 250** | Next 250 after the FTSE 100. Ticker: `^FTMC`. |
| **S&P 500** | Top 500 US-listed companies. Ticker: `^GSPC`. |
| **OHLCV** | Open / High / Low / Close / Volume — the five fields in a bar of price data. |
| **Bar / candle** | One period of OHLCV — could be a day, an hour, a minute. This app uses daily bars today. |
| **Stamp duty** | UK tax of 0.5% on buying most LSE main-market shares. AIM shares and ETFs are exempt. |
| **Commission** | Flat fee broker charges per trade. |
| **Slippage** | Difference between expected fill price and actual fill. We don't model it today. |
| **ISA** (Individual Savings Account) | UK tax-free wrapper; up to £20,000/year contributions, gains + dividends tax-free. Planned feature: ISA mode in simulations. |
| **AIM** (Alternative Investment Market) | LSE's growth-company market. Shares are exempt from stamp duty and may qualify for BPR inheritance-tax relief. |

### Time horizons (how we classify strategies)

| Label | Typical holding period | Example strategies |
|---|---|---|
| **Intraday** | Minutes to hours, flat by close | None today |
| **Short-term** | Days to a couple of weeks | RSI mean-reversion, tight SMA crossover (5/10) |
| **Mid-term** | A few weeks to a few months | SMA crossover (20/50), MACD, Donchian |
| **Long-term** | Months to years | Buy & Hold, SMA crossover (50/200) |

### App-specific

| Term | Meaning |
|---|---|
| **Watchlist** | A named list of symbols to run the scanner over (e.g. `uk`, `uk_ftse100_sample`). |
| **Scanner** | The "what's worth buying or selling today?" view — runs a strategy over a watchlist and ranks the results. |
| **Signal detail** | Single-symbol view of the current BUY/SELL/HOLD call, with indicators and historical hit-rate. |
| **Hit-rate card** | On the Signal detail page: win rate, expectancy, median hold, best/worst trades over the last 10 years. |
| **Run ID** | Unique UUID for each backtest/simulation, used to trace a number on a chart back to its exact inputs. |
| **Worker** | `tradepro-worker` process running on your Mac that picks up deep-backtest jobs from Firestore. |
| **Cache** | Local Parquet files per `(provider, symbol, interval)` on the Mac under `~/.tradepro/cache`. |
