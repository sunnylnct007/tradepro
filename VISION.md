# TradePro — Unicorn-grade architecture for algo + systematic trading

> *"This platform needs to be a unicorn in its architecture for algo
> trading and systematic trading."*

This doc sets the bar. Every architectural decision after this point —
the DB schema, the message bus, the order management system, the
backtest engine — gets measured against the principles here.
[ROADMAP.md](ROADMAP.md) tracks the phasing; this doc explains the
*why* behind each phase so we don't drift.

## What "unicorn architecture" actually means

The word gets thrown around. Here it has a specific meaning: an
architecture you'd see in a top-tier quant fund or a serious retail-
systematic platform (think Tradetron, QuantConnect, Composer at
their best). The defining properties:

1. **Every order has an immutable, auditable trail.** Who emitted
   it, when, with what facts, what happened next. Reproducible from
   inputs.
2. **Every strategy is versioned and reproducible.** Today's signal
   from `donchian_breakout v1.3` is bit-for-bit reproducible
   tomorrow even if v1.4 has shipped.
3. **State survives all failures.** Process crash, EC2 reboot, full
   account wipe — the recoverable state lets you reconstruct
   positions, P&L, open orders, pending decisions.
4. **Backtests and live trading run the same code path.** A
   strategy that performs in backtest must behave identically in
   paper-trading; any divergence is a *bug*, not a feature.
5. **Risk runs as a separate engine.** Strategy says BUY; risk
   engine says "no, you'd exceed the position cap". Strategy
   doesn't get to override risk.
6. **Multi-broker, multi-venue, multi-asset is a deployment
   choice, not a code change.** Add a new broker = implement one
   interface, register, done.
7. **Observability is first-class.** Every order, every fill,
   every P&L delta, every strategy decision — all visible in
   real-time with traceable causation.
8. **The system has clear "regime" awareness.** Strategy weights,
   risk caps, and even the active strategy set can shift with
   detected regime.

If we can check all eight boxes, the platform is genuinely
unicorn-tier in *architecture* (separate from "is it commercially
successful"). The bar is high but achievable.

## Where we are vs where we need to go

| Property | Today | Gap |
|---|---|---|
| Immutable order trail | JSONL on Mac + in-memory store on EC2 (lost on redeploy) | **Need event-sourced order log in Postgres** |
| Strategy versioning | git SHA in manifest, hand-tracked | Need versioned strategy registry; signal output keyed by `(strategy, version, params_hash)` |
| State survives failures | Compare cache + paper-engine JSONL persist; pending orders, settings, snapshots, watchlists all volatile | **All stores → Postgres** |
| Backtest == live code path | Paper-engine deliberately shares the same strategy code; daily simulator has a separate path | Unify: one engine, two modes (replay vs live) — already 80% there |
| Risk as separate engine | `RiskLimits` enforced inside the engine — not separable | Lift into a `RiskService` with its own state and reject/approve flow |
| Multi-broker abstraction | T212 + IBKR + replay + yfinance + stub_live — well-factored | Mostly done; needs a generic Order/Fill/Position schema |
| Observability | JSONL logs + email digest + Paper page | Add a real-time event stream (Server-Sent Events from API), structured event log in DB |
| Regime awareness | Detected and shown in decision-trace; doesn't influence consensus weights | Wire regime → strategy weights in the consensus engine |

## The arc

A pragmatic, phased path. Each phase ships a concrete improvement
and doesn't require the next phase to be valuable on its own.

### Phase 5 — Postgres migration (current, ~3-5 days)

Move every store off in-memory + JSON files onto Postgres. Schema
designed for event sourcing on the order-flow side and relational
modelling everywhere else. Detailed in
[ARCHITECTURE.md](docs/ARCHITECTURE.md) §Postgres-schema once
written.

Tables (initial set):

- `orders` — every order intent emitted by any strategy, immutable.
- `fills` — every execution against an order, immutable.
- `positions` — derived view over orders + fills, current state.
- `pending_orders` — manual-mode queue replacing
  `IPendingOrdersStore`.
- `paper_sessions` — replaces `IPaperSnapshotStore`.
- `paper_backtests` — replaces `IPaperBacktestStore`.
- `strategy_versions` — versioned registry of strategies.
- `strategy_runs` — every (strategy_version, symbol, params,
  start, end) backtest + its full output.
- `compare_cache` — replaces file-based compare store.
- `watchlists` — replaces `InMemoryWatchlistStore`.
- `settings` — replaces `FileSettingsStore`.
- `documents` + `document_text` — replaces `FileDocumentStore`.
- `heartbeats` — replaces `IHeartbeatStore`.
- `events` — append-only domain event log (for the observability
  stream in Phase 7).

### Phase 6 — Risk engine separation (~3-5 days)

Lift `RiskLimits` out of the engine into a `RiskService`. Every
order intent flows through risk before reaching the broker.
Risk has its own DB state (current exposures, daily P&L, drawdown
guards) and can reject orders even when the strategy is willing.
Surfaces "rejected by risk" rows on the Paper page.

### Phase 7 — Real-time event stream (~2-3 days)

Server-Sent Events from the API: every order, fill, P&L delta,
heartbeat, sentiment update, regime change emits an event. Frontend
subscribes; the Paper page Live tab and the Decide page become
live-updating without polling. Foundation for monitoring + alerts.

### Phase 8 — Strategy versioning + plugin API (~5-7 days)

A strategy is `(name, version, code_hash, params_schema)`. The
registry stores all versions. Backtests run against a specific
version. Decide-page signals carry the version they came from.
Third-party authors can drop a strategy into `strategies/plugins/`,
the registry picks it up via Python entry points.

### Phase 9 — Regime-weighted consensus (~3-5 days)

The market regime detector already exists in `market_state.py`.
Today the consensus engine ignores it. After Phase 9, each
strategy's vote is weighted by its historical Sharpe in the
currently-detected regime, normalised across strategies. Strategies
with negative regime Sharpe contribute zero. Settles the MTUM/RSI-MR
class of contradictions at the consensus layer.

### Phase 10 — Walk-forward backtest framework (~5-7 days)

Today every backtest fits parameters on the same data it reports
performance on — classic overfit risk. Walk-forward splits the
data into rolling train/test windows; parameters are picked on
train, performance reported on (out-of-sample) test. The Backtest
page shows both in-sample and out-of-sample stats per strategy.

### Phase 11 — Live-vs-backtest drift dashboard (~3-5 days)

The data exists post-Phase 5 (paper-trading P&L in `fills` +
backtest predictions in `strategy_runs`). Build a panel that
shows live Sharpe / backtest Sharpe / drift % per (strategy,
symbol). Alerts on drift > 50%.

### Phase 12 — Multi-asset extension (~2-3 weeks)

Today: equities + ETFs only. Phase 12 adds futures (CME via IBKR),
FX (IBKR), and crypto (existing Binance code, currently disabled).
Each asset class needs its own contract sizing, margin rules, and
data adapter. Schema is already polymorphic enough to support this
post-Phase 5.

## What we explicitly are NOT doing

The unicorn bar is high, but some things are explicitly out of
scope:

- **HFT-level latency**. We're a minute-bar / daily platform. If
  we ever go to second-bar, that's a different system.
- **Co-location / exchange-direct connectivity**. The brokers
  (T212, IBKR) are the abstraction.
- **Multi-tenant**. Single-user platform; multi-user adds whole
  classes of complexity (auth, billing, isolation) that hurt the
  architecture if added prematurely.
- **Custom analytics languages** (à la QuantConnect's algo
  scripting). Strategies are Python (paper) or C# (signal),
  full stop.
- **In-house exchange feeds**. Yahoo + Finnhub + broker feeds are
  it. A direct exchange feed (e.g. Polygon) might come in Phase
  13+ if needed.

## Principles for every PR

1. **No new in-memory store.** If state needs to survive a
   redeploy, it goes in Postgres. No exceptions.
2. **No new file-backed JSON store.** Same reason. Files are for
   immutable artefacts (parquet caches, JSONL logs), not active
   state.
3. **Every order has a `decision_trace`.** Why was this BUY
   emitted? What facts? What strategy version? Append-only.
4. **Backtest and paper-trading share the strategy code.** If
   they don't, that's a bug.
5. **Risk decisions are logged.** Even a "risk says no" is an
   event worth recording.
6. **No magic constants.** Strategy params, risk caps, regime
   thresholds — all configurable, all versioned, all visible.

## Where to look

- [STRATEGIES.md](STRATEGIES.md) — the strategy layers
- [EVALUATION.md](EVALUATION.md) — how to tell if a strategy works
- [ROADMAP.md](ROADMAP.md) — phasing
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — current system
- [strategies/PAPER_TRADING.md](strategies/PAPER_TRADING.md) — paper engine
- [strategies/CONCEPTS.md](strategies/CONCEPTS.md) — glossary
