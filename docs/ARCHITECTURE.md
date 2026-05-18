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

> **Hosting moved May 2026.** Frontend + API now both run on a single
> AWS t4g.small in eu-west-2, fronted by Caddy (TLS) + nginx. Firebase
> Hosting + Azure App Service are decommissioned. The live AWS diagram
> is at `docs/aws-architecture.md` — that's the canonical source for
> infra; this section keeps the stack rationale.

| Layer | Service | Why |
|---|---|---|
| Web UI | React + Vite, served by nginx in a docker container on the AWS host. Reachable at `https://tradepro.showsoldprice.com` | Static bundle is cheap; nginx handles the SPA fallback + reverse-proxies `/api/*` to the .NET container over the docker network |
| TLS / reverse proxy | Caddy 2 on the same host | Auto-fetches Let's Encrypt certs via HTTP-01, auto-renews every 60 days, costs £0. HSTS + HTTP/3 out of the box |
| Auth (SPA shell) | nginx Basic Auth (htpasswd) | One-credential demo gate. Firebase auth is wired in code but turned off (`FIREBASE_REQUIRE_AUTH=false`) for the demo |
| Auth (worker → API) | Bearer `INGEST_TOKEN` on `/api/ingest/*` | The worker pushes compare + heartbeat payloads; no Firebase round-trip needed |
| API | .NET 8 minimal API in a docker container on the AWS host | Strongly-typed domain; ARM64 image (Graviton); ~£3-4/mo with nightly auto-stop |
| Persistent state | Per-universe JSON file store on a named docker volume `tradepro_api_compare_cache` mounted at `/data/compare`; chowned to the API container's app user at startup via a busybox init container | Single-tenant, no managed-DB cost; survives redeploys (post-May 2026 chown fix) |
| Research & heavy compute | Python (`tradepro_strategies`) on a MacBook M4 | Vectorised pandas/numpy is fast; local LLM (Ollama llama3.1:8b) for rationales keeps inference cost at £0 |
| Data | Yahoo Finance (default), Trading 212 (portfolio + instruments), Finnhub (earnings calendar — off unless `TRADEPRO_FINNHUB_API_KEY` set). Stooq + Binance present but disabled. | Free or near-free; T212 key+secret pair lives only on the AWS host's `/opt/tradepro/.env`, written via the `aws-set-env` workflow |

Cost target: **~£3-4/month** for a single user (EC2 + EBS only, EIP free
while attached). The LLM stays on the Mac so AWS doesn't need GPU.

---

## 3. Components and how they talk

```
                       ┌──────────────────────────┐
                       │  Browser (React)         │
                       │  http://16.60.201.137/   │
                       └────────────┬─────────────┘
                                    │  HTTPS + (optional)
                                    │  Firebase ID token
                                    ▼
                       ┌──────────────────────────┐
                       │  EC2 t4g.small (eu-west-2)│
                       │  ┌────────────────────┐  │
                       │  │ Caddy 2 (TLS)      │  │
                       │  ├────────────────────┤  │
                       │  │ nginx (SPA + /api) │  │
                       │  ├────────────────────┤  │
                       │  │ .NET 8 API         │  │
                       │  │  · JSON stores on  │  │
                       │  │    EBS volume      │  │
                       │  │  · reads SM bundle │  │
                       │  │    at boot         │  │
                       │  └────────────────────┘  │
                       └─────▲──────────▲─────────┘
                             │          │
                  /api/ingest│          │ secretsmanager:
                  Bearer     │          │  GetSecretValue
                  INGEST_TOKEN          │
                             │          ▼
                             │   ┌────────────────┐
                             │   │ AWS Secrets    │
                             │   │ Manager        │
                             │   │ tradepro/all   │
                             │   │ (eu-north-1)   │
                             │   └────────────────┘
                  ┌──────────┴─────────────┐
                  │  Mac (M4) — Python     │
                  │   tradepro-refresh     │  launchd, 5x/day
                  │   tradepro-paper       │  manual, paper sessions
                  │   tradepro-email       │  launchd, 23:00 UTC
                  │   Parquet cache        │  ~/.tradepro/cache
                  │   Ollama (llama3.1)    │  rationale + sentiment
                  └──────────┬─────────────┘
                             │ outbound only
                             ▼
                  ┌──────────────────────────┐
                  │  Yahoo · Finnhub · T212  │
                  └──────────────────────────┘
```

Key properties:
- **The website never opens a connection into the Mac.** All Mac
  traffic is outbound.
- **The Mac is the active producer.** It runs scheduled refreshes
  (compare cache), paper-trading sessions, the daily email digest,
  and rationale/sentiment LLM inference. The API on EC2 is a
  passive store + execution endpoint.
- **No Firestore.** The old jobs-queue pattern (browser writes a
  doc, Mac listens, writes back result) was removed in the May
  2026 migration. The current flow is: Mac pushes payloads to the
  API on its own schedule; the browser reads from the API.

---

## 4. Data flow — "what should I buy today?" (Decide page)

1. User opens `/decide`. Frontend hits
   `GET /api/compare/latest?universe=etf_all`.
2. API reads the freshest compare cache from the JSON store (one
   file per universe, written by the Mac's last refresh).
3. Per-row payload: symbol, strategy, current_action, in_position,
   stats, rationale, sentiment, fundamentals, horizon scores,
   gems data.
4. Frontend filters by horizon pill (Swing / Long-term / Passive)
   and ranks BUY / WAIT / AVOID buckets.

Latency: ~50ms for a typical universe (the file is already on
disk; no Yahoo fetch involved). The "freshness" badge shows when
the Mac last refreshed — typically less than 4 hours given the
6:30 / 10:30 / 14:30 / 18:30 / 22:30 UTC schedule.

---

## 5. Data flow — paper-trading sessions

There are two paper-trading flows: **auto placement** (router
posts directly to T212) and **manual placement** (router pushes
intent to the API queue, user clicks Approve in the UI).

**Auto mode:**
1. Mac runs `tradepro-paper --broker t212 --placement-mode auto`.
2. Strategy emits an order intent.
3. T212OrderRouter on the Mac uses the SM-bundle T212 key to call
   `POST https://demo.trading212.com/api/v0/equity/orders/market`.
4. Fills come back via the broker's order-stream and update the
   Mac's in-memory ledger.
5. At session end, Mac POSTs the ledger snapshot to
   `/api/ingest/paper-snapshot`. Frontend Paper page Live tab
   reads it.

**Manual mode:**
1. Same as auto through step 2.
2. T212OrderRouter on the Mac POSTs the intent to
   `/api/ingest/paper-pending-order`.
3. API stores in `IPendingOrdersStore` (in-memory, capped at 200).
4. User opens Paper page → Pending Orders tab → clicks Approve.
5. API calls `Trading212Client.PlaceMarketOrderAsync` (uses the
   SM-bundle T212 key on EC2, not Mac).
6. API stores Placed / Failed state with the broker order id.

The manual flow keeps a human in the loop while letting the Mac
engine sleep — once an order is pushed, the Mac doesn't need to
stay running for the order to execute.

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

3. **API-side compare cache** for results pushed up by the Mac:
   per-universe JSON files at `/data/compare/<universe>.json` on
   the EC2 host (mounted from a docker volume). Each row carries
   the `manifest` blob, so any number in the UI traces back to a
   `run_id` on the Mac.

Given a number on a chart, you can:
- Find the `run_id` in the row's manifest (visible in the Decide
  page's decision-trace panel) or in the Mac artefact dir.
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
  `Firebase__AllowedUserIds` pass authorization (when Firebase auth
  is enabled; the demo currently runs with `FIREBASE_REQUIRE_AUTH=false`
  behind nginx Basic Auth instead).
- **Ingest endpoints** (`/api/ingest/*`) require a static Bearer
  token (`Ingest__Token`, loaded from SM bundle at boot). Only the
  Mac knows this token, so the worker push path is gated separately
  from the user-facing API.
- **Secrets at rest** live in **AWS Secrets Manager** at
  `tradepro/all` (eu-north-1). Both the Mac engine and the EC2 API
  read the bundle via SDK. The EC2 instance role has read-only
  access via the `ccit-dev-tradepro-ec2-secrets-bundle-read` inline
  policy. No secret value ever lives in git, in tfstate, or in the
  docker image.
- **Firebase web config is not a secret** — it ships in the bundle.
  Lock it down by adding HTTP-referrer restrictions in Google Cloud
  Console.
- **Never commit**: AWS access keys, GitHub PATs, T212 keys, ingest
  tokens, Firebase Admin keys, `.env.local`, anything in `~/.tradepro/`.

---

## 12. Deployment surface

| Target | Trigger | Workflow |
|---|---|---|
| Firebase Hosting (live) | push to `main` touching `frontend/**` | `.github/workflows/firebase-hosting-deploy.yml` |
| Firebase Hosting (preview) | open/update PR touching `frontend/**` | `.github/workflows/firebase-hosting-preview.yml` |
| EC2 docker redeploy | push to `main` touching `backend/**` or manual `gh workflow run aws-redeploy.yml` | `.github/workflows/aws-redeploy.yml` |
| EC2 lifecycle (start/stop) | manual or scheduled | `.github/workflows/aws-start.yml`, scheduled auto-stop in TF module |
| Terraform infra | manual `terraform apply` from `ccit-infra/accounts/infoccit-workloads/` | none — applied locally |

Secrets in GitHub repo:
- `FIREBASE_SERVICE_ACCOUNT_SMSP_291E3` — deploy Firebase Hosting.
- `INGEST_TOKEN`, `T212_API_KEY`, `T212_API_SECRET`, `FINNHUB_API_KEY`,
  `BASIC_AUTH_HTPASSWD`, `T212_MODE` — historical, used by the
  `aws-set-env.yml` workflow to seed an EC2 `.env` file. Will be
  retired once the .NET API reads everything from the SM bundle
  (just shipped; some still wired through env vars in compose).

Secrets at runtime (read at boot):
- **Mac**: `tradepro_strategies.secrets.get_secret()` → SM bundle
  `tradepro/all` (eu-north-1) → fallback `~/.tradepro/credentials`.
- **EC2 .NET API**: `SecretsBundleLoader.LoadInto()` →
  `Trading212:ApiKey`, `Finnhub:ApiKey`, `Ingest:Token`, etc.

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
| **Intraday** | Minutes to hours, flat by close | ORB, VWAP-MR, BollingerBounce intraday, EMA crossover (Python paper-trading engine) |
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
| **Worker** | Generic name for any scheduled Mac-side process that pushes data to the API. Today: `tradepro-refresh` (compare cache, 5×/day), `tradepro-email` (digest, 23:00 UTC), `tradepro-heartbeat` (worker state). Plus manual: `tradepro-paper` (paper-trading sessions). |
| **Cache** | Local Parquet files per `(provider, symbol, interval)` on the Mac under `~/.tradepro/cache`. |
