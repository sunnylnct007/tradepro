# TradePro concepts — what each term means and where it lives

Glossary + architecture map. If a UI label or log message confuses
you, this is where to look first.

---

## The two engines

TradePro runs two independent engines side-by-side:

| Engine | What it answers | Timeframe | Code location |
|---|---|---|---|
| **Daily engine** (existing) | "Should I invest in this asset?" | Daily bars, multi-year | `tradepro_strategies/strategies/`, `compare.py` |
| **Paper engine** (new) | "Would this intraday strategy have made money?" | 1-minute bars, per-session | `tradepro_strategies/paper/` |

They share **no runtime code**. They're separate on purpose — daily-
horizon investing and intraday day-trading have different lifecycles,
different bar sources, different risk envelopes.

---

## Pages and what they actually do

| Page | URL | What it shows | Data source |
|---|---|---|---|
| **Decide** | `/compare` | "Should I invest today?" — bucket (BUY/WAIT/AVOID) per asset in a universe | Cached worker-refresh snapshot |
| **Research** | `/signals` | Live single-symbol verdict with full indicators | Live API call, computed on-demand |
| **Backtest** | `/simulations` | Daily-engine historical equity curve for one strategy | Live computation |
| **Paper** | `/paper-backtest` | Intraday paper-engine walk-forward + comparator results | Mac-pushed reports |
| **Portfolio** | `/portfolio` | T212 holdings + Decide bucket alignment | T212 API + cached snapshot |
| **Scanner** | `/scanner` | Single-strategy scan across a universe | Live computation |
| **Health** | `/health` | Worker / API / integrations status | Live |

**Decide vs Research** (why the same symbol can show different verdicts):
- **Decide** reads cached refresh data (0–24h old) and applies *additional* filters: sentiment demotion, horizon split, range veto.
- **Research** runs strategies live on-demand, no caching, no filters.

So a symbol can be BUY on Research and WAIT on Decide because (a) the filters demoted it, or (b) the snapshot is from different price action than now.

---

## Bucket states (Decide page)

Every asset gets one of three labels:

| Bucket | What it means |
|---|---|
| **BUY** | Most strategies long + price action healthy + no demotions triggered |
| **WAIT** | Signal was BUY but a filter demoted it (sentiment, horizon, range), OR signal is genuinely mixed |
| **AVOID** | Strategies divided/negative OR a hard filter fired (e.g., price < 200d SMA) |

The server (Python `compare.py`) makes this decision; the UI must NOT re-derive it. There's a comment in `Compare.tsx:286` enforcing this — Bug TSLA #2 came from the UI second-guessing the server.

---

## Horizons (Decide page)

Every symbol gets three independent verdicts at three time scales:

| Horizon | Window | What it measures |
|---|---|---|
| **Swing** | 1–8 weeks | RSI + composite swing score + range position |
| **Long-term** | 6–18 months | Sharpe, valuation flag, analyst upside, momentum |
| **Passive** | 3–5 years | Expense ratio, holdings count, dividend yield (ETFs) |

After **Bug #11 fix** (now): the Swing horizon uses the *same* composite swing score as the SwingScoreCard. They can't disagree by construction. Range-position modifier still applies on top (near highs → cap at WATCH).

Click any horizon pill at the top of Decide to **filter** the matrix to symbols that are BUY at that horizon.

---

## Strategies (daily engine — 7 of them)

Lives in `tradepro_strategies/strategies/`. Each is a class implementing `evaluate(symbol_state) → StrategyResult`. They run on daily bars and emit a long/flat verdict.

| Name | Family | Signal |
|---|---|---|
| `sma_crossover` | Price-vs-MA | Fast SMA > slow SMA |
| `breakout` | Price-vs-MA | Close > N-day high |
| `mean_reversion` | Mean revert | RSI oversold + price < SMA |
| `momentum` | Price-vs-MA | 12-month return rank |
| `value_dividend` | Fundamental | High yield + sustainable cover |
| `ichimoku_cloud` | Price-vs-MA | Tenkan/Kijun cross + cloud break |
| `bollinger_bounce` | Mean revert | Price tags + rejects lower band |

UI dropdown calls these "strategies". The Compare row's `long_count / total_strategies` is the consensus vote across all 7.

---

## Strategies (paper engine — 4 of them, intraday)

Lives in `tradepro_strategies/paper/strategies/`. Each subclasses `Strategy` and implements `on_bar(bar)`. Registered via `@register_strategy("name")`.

| Registry name | Family | Thesis | When it works | When it fails |
|---|---|---|---|---|
| `orb` | Breakout | Price breaks first-15-min range → run | Trending opens, gap-and-go | Chop / range days |
| `vwap_mean_reversion` | Mean revert | Fade divergence from session VWAP | Range-bound chop | Strong-trend days |
| `bollinger_bounce` | Mean revert (adaptive) | Touch + reject outer Bollinger band | Stable-vol chop | Trends / squeeze-expansion |
| `ma_crossover` | Trend follow | Fast EMA crosses slow EMA | Persistent intraday trends | Whipsaw days |

Register a new one: subclass `Strategy`, decorate with `@register_strategy("name")`, drop in `paper/strategies/`. Run `tradepro-paper-strategies-push` so the UI catalog picks it up.

---

## Paper-trading engine — service boundaries

The paper engine is a **modular monolith** wired with hard service boundaries (asyncio queues today, Redis Streams tomorrow). Each box below is its own coroutine; they communicate only via the queues.

```
BarBus → Strategy → RiskService → OrderRouter → Ledger
         ↘─ on_fill ←──── FillEvent ────────────────┘
```

| Service | What it owns | Today | Tomorrow |
|---|---|---|---|
| **BarBus** | "What bars exist for which symbols" | `ReplayBarBus`, `SourceBackedBus` (Yahoo + Finnhub + cache) | `LiveIBKRBarBus` |
| **Strategy** | "Given a bar, emit zero-or-more orders" | `Strategy` ABC + concrete subclasses | Same (split into containers per strategy) |
| **RiskService** | "Is this order allowed?" | `check_order` — sizing caps, allow_short, halt | Same, plus cross-strategy correlation |
| **OrderRouter** | "Turn approved orders into fills" | `PaperOrderRouter`, `T212OrderRouter`, `IBKRRouter`, `MultiBrokerRouter` | More routers; webhook-driven fills |
| **Ledger** | "Per-strategy P&L attribution" | `Ledger` — FIFO realised, MTM unrealised | Persisted to Postgres |

**Why this split matters:** swapping `PaperOrderRouter` for `T212OrderRouter` is a one-line change in the `Engine` constructor. Strategies and risk don't change. Same applies to the bar bus.

---

## Validator vs Comparator (paper engine)

| Tool | Question | CLI |
|---|---|---|
| **Validator** | "Would strategy X have made money on symbol Y from date A to date B?" | `tradepro-paper-backtest --strategy X --symbol Y --from A --to B` |
| **Comparator** | "Of these N strategies, which is best?" | `tradepro-paper-compare --entry "Label1::strat1" --entry "Label2::strat2" ...` |

Both run sessions sequentially via the engine. After the first session, every subsequent session hits the **parquet cache** so multi-strategy or multi-tuning re-runs are free.

Single point-in-time backtest = a 1-day validator: `--date 2026-05-15` instead of `--from / --to`.

---

## Bar sources — cache + fallback

```
SourceBackedBus(
  FallbackSource([
    CachedSource(YfinanceSource()),   # Try Yahoo, persist to parquet
    CachedSource(FinnhubSource()),    # Fall back if Yahoo missing/throttled
  ])
)
```

- **CachedSource** writes one parquet file per `(symbol, interval, date)` under `~/.tradepro/cache/intraday/`. Read-through: cache hit skips the network entirely.
- **FallbackSource** walks each source in order; first non-empty result wins. Used because Yahoo's 1m intraday window is only ~30 days.
- **FinnhubSource** needs `TRADEPRO_FINNHUB_API_KEY`; otherwise silently returns `[]` and the chain continues.

---

## Broker profiles (paper engine)

Each `--broker` CLI flag picks both the BarBus and the OrderRouter:

| Profile | Bars | Orders fill | Real money? |
|---|---|---|---|
| `replay` | in-memory list | `PaperOrderRouter` | No |
| `yfinance` | Yahoo+cache+Finnhub | `PaperOrderRouter` | No |
| `t212` | Yahoo+cache+Finnhub (T212 has no bars) | `T212OrderRouter` | YES — demo or live |
| `ibkr` | IBKR Gateway | `IBKRRouter` | YES — paper or live |
| `stub_live` | Yahoo+cache+Finnhub | `StubLiveRouter` (logs, never fills) | No |

**Two-key safety gate for live money:** both `--allow-real-orders` AND the env var (`TRADEPRO_T212_ALLOW_LIVE=1` or `TRADEPRO_IBKR_ALLOW_LIVE=1`) must be set. Either missing → orders logged as `WOULD-PLACE`, never sent.

---

## Order types

Today: **MARKET only**, everywhere. Limit / stop / bracket need a working-orders queue + bar-driven matching that hasn't landed yet — will when a strategy actually needs them.

---

## Look-ahead-bias prevention

The `PaperOrderRouter` fills MARKET orders at the **next bar's open** with slippage applied against direction. The router enforces this via `if msg.bar.timestamp <= approval.bar_at_approval.timestamp: skip`. Without that check, a fanout scheduling race let orders fill at the SAME bar they were emitted on (the bug that initially made backtests look profitable when they weren't).

---

## Risk envelope (RiskLimits)

Per-strategy guards live in `paper/risk.py`:

| Field | Effect |
|---|---|
| `max_position_value_usd` | Hard cap on \|position_value\| at submission |
| `max_position_pct_of_capital` | Soft cap as fraction of sub-account allocation |
| `max_open_positions` | Concurrent symbol cap |
| `allow_short` | False (default) → reject any order that opens/extends a short |
| `max_daily_loss_usd` | Halt strategy for session when daily P&L drops below |
| `max_drawdown_pct` | Halt when equity falls > X% from peak |

After every fill, the Ledger calls `update_pnl_and_check_halt` on the RiskService so halts trip in real time, not at session-end.

---

## Plug-in registry

Three ways to register a new strategy:

1. **`@register_strategy("name")`** — decorator above the class (in-tree)
2. **setuptools entry point** in your own pip package's `pyproject.toml`:
   ```toml
   [project.entry-points."tradepro.strategies"]
   my_name = "my_pkg.module:MyClass"
   ```
3. **CLI dynamic import**: `--strategy-class my_pkg.module:MyClass` (ad-hoc)

All three go through the same `paper.registry.get(name) → StrategySpec` lookup.

---

## Worker push model

The Mac is the active worker; the API is passive storage. Mac pushes results via authenticated POST to `/api/ingest/<kind>`:

| Kind | Pusher | Reader |
|---|---|---|
| `compare` | `tradepro-compare --push` (daily refresh) | Decide page |
| `heartbeat` | `tradepro-heartbeat` (every 15m via launchd) | Worker badge |
| `document` | `tradepro-doc-upload` | Documents page |
| `paper-backtest` | `tradepro-paper-backtest --push` / `tradepro-paper-compare --push` | Paper page |
| `paper-strategies` | `tradepro-paper-strategies-push` | Paper page catalog |

Ingest endpoint uses a bearer token (`Ingest:Token`), separate from the Firebase login the UI uses. There's NO API → Mac path — when the UI needs to trigger work on the Mac, the model is: API queues a job → worker polls → worker pushes result. (Not yet built; today everything is terminal-triggered.)

---

## See also

- `PAPER_TRADING.md` — every CLI command, env var, and broker option
- `tradepro_strategies/paper/__init__.py` — top-level imports + roadmap
- Each module's docstring carries WHY-style commentary, not just WHAT
