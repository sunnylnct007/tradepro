# TradePro Roadmap

A personal, evidence-based trading-strategy platform. The product
question:

> **"Today, should I BUY / WAIT / AVOID, and which ETF?"**
> — backed by backtest evidence, per-regime stress survival, and a
> cross-check against analyst consensus.

This document is the load-bearing plan: where we are, where we're
going, and the assumptions baked into every choice. Update it when
those assumptions change.

---

## NEW WORKSTREAM — Options trading framework (2026-05-30, SRS received)

Goal NOW: **signals + display potential opportunities only — NO execution**
(no options broker account yet). Plus a robust risk module, stress testing,
backtesting, and an LLM-verified quality gate (same gate planned for the
other strategies). Operator supplied a full SRS; key decisions to make.

**Reuse, don't rebuild.** The SRS proposes 3 new microservices (Python
Quant, C# Risk/Execution, C# Portfolio State) + EF Core + MS SQL +
RabbitMQ/Azure. TradePro ALREADY is that shape — Python strategies (signals)
→ .NET API (OMS + RiskGate + positions) → Postgres, with an ops queue. So
EXTEND the existing system; do NOT spin up parallel microservices or switch
to EF/MSSQL (we're Dapper/Postgres on purpose).

**Maps onto existing components:**
- Universe SPY/QQQ/IWM/GLD/TLT/XLF — overlaps us_equity_core (add GLD/TLT).
- `BlackScholesPricer` (scipy.stats.norm) — NEW, quant_engine: fair value +
  Greeks (Δ/Θ/Vega) to verify vs broker/source quotes.
- Strategies: Tier-1 Bull Put Spread (200SMA touch + IVR>30; sell 30Δ / buy
  15Δ put); Tier-2 Iron Condor (IVR>50; sell 20Δ / buy 10Δ, ~45 DTE) — new
  multi-leg paper strategies.
- Liquidity gate: ATM bid-ask ≤ $0.05 or 2% of mid, else ban — pre-signal.
- Stress: quant_engine/monte_carlo.py exists (10k GBM paths) + a daily
  deterministic shock (−20% spot, +50% IV) on open portfolio.
- Risk module → extend .NET RiskGate: max-loss > 3% equity → reject; BPR
  (margin) > 30% equity → reject (keep 70% cash for vol expansion); beta-
  weighted portfolio Δ vs SPY extreme → reject. (Mirrors VRP/defined-risk.)
- LLM quality gate → reuse the existing LLM signal gate.
- Execution (limit-only, async, multi-leg via IBKR/Tradier) — DEFERRED
  (no broker). When it lands: limit @ mid, never market.

**Data:** SRS wants ORATS (paid, pre-computed Greeks). We don't have it.
Bootstrap signals/display with FREE option chains (yfinance) + self-computed
Greeks (Black-Scholes); data provider is pluggable → swap to ORATS later.

**Phasing (no execution):** P0 data+pricer+Greeks+IVR → P1 signal gen (the
2 strategies) → P2 display "opportunities" (new Options surface/desk) →
P3 risk gates (annotate, no place) → P4 stress + backtest → P5 execution
(when a broker account exists).

**DECIDED defaults (operator 2026-05-31) — PROVISIONAL, MUST REVISIT & OPTIMIZE:**
Building P1 on: free yfinance option chains + Greeks WE compute (Black-
Scholes) · NO execution (signals + opportunity display only) · forward
paper-signal logging (no paid historical options backtest yet) · reuse the
existing Python+.NET+Postgres arch. ⚠️ These are bootstrapping choices to
get signals flowing — REVISIT before any real reliance:
  - **Data quality:** yfinance chains are delayed/patchy + IV can be
    missing → our Greeks are model-derived, not market-verified. Swap to
    ORATS (or a paid chains+Greeks feed) for accuracy before execution.
  - **Backtest fidelity:** underlying-only / forward paper signals can't
    measure real fill/spread/assignment economics — needs historical
    options data for a true VRP backtest.
  - **Pricing model:** Black-Scholes (European) on American ETF options is
    fine for Greeks/triage, NOT for early-exercise/wing edge.
  - **Risk inputs:** BPR/margin + beta-weighted Δ need real broker margin
    data; until execution, these are estimates.
  - Re-evaluate the whole data→signal→risk→backtest chain once a paid
    feed + (eventually) a broker account land.

---

## 🔴 CRITICAL — zero-fill since IG/T212 migration (MCP analysis 2026-05-31)

Across 32 sessions / 12 days, only **7 fills exist — all from ONE
ichimoku_fx_mr run on 25 May on the old yfinance backend**. Since migrating
to IG/T212, **nothing has filled.** Signal logic clearly fires (those fills
prove it), so the break is in **broker connectivity / placement / fill-
recording**, not signals. Diagnose Monday (markets open) — likely a stack
of causes we've already partly touched:
- **FX was mis-routed to T212** (can't trade FX) → all CANCELLED. Migration
  028 now routes ichimoku_fx_mr → IG; verify it actually fills on IG Monday.
- **Equity (T212):** orders sit SUBMITTED, never FILLED → is the T212 demo
  **fill poller** running/recording? Are they MOO orders waiting for open?
- **IG fill poller** records at price=0 + only on a timely poll — verify.
- **Placement mode:** confirm auto vs manual (manual → orders sit pending).
- Weekend caveat: markets closed now, so "no fills this weekend" is partly
  expected — the 12-day window is the real signal.
- **Mac worker / MCP offline** (get_health timed out) — restart Claude
  Desktop + the TradePro MCP server before Monday open.
This is the #1 thing blocking the platform from actually trading.

### Other strategies (from the analysis)
- **ORB** — 8 sessions, 0 fills, no caveats = untested scaffold. Verify
  symbols, the 15-min range window vs BST open, and whether $100 risk is
  reachable given symbol ATR.
- **compass_momentum** — best next activation (gates on COMPASS ≥68 →
  TradePro-native, not a generic indicator strategy).

## UI — mobile responsive (2026-05-31)
Cockpit isn't mobile-friendly: header (nav + badges) and the panel grid
don't align/wrap on small screens. Needs responsive breakpoints (stacked
header, single-column grid, fluid tables).

## Risk module — per-order validation display + tiered rules (2026-05-31)
Infra EXISTS (.NET RiskGate: blacklist / size-cap / velocity / sentiment /
cash / market-hours / broker-capability gates; risk_events; /api/risk
audit; RiskMonitorService). Needed:
- **Show per-order risk validation** for a strategy's order — which gates
  ran, pass/fail, the numbers (capital, max-loss, notional, BPR, beta-Δ) —
  so we SEE the risk module working. Surface risk_events / decision-trace.
- **Tiered rules:** a few GLOBAL rules + per-STRATEGY overrides, adjustable
  per strategy (options max-loss/BPR vs equity size-cap differ). Settings-
  driven (app_settings_kv + a per-strategy config like strategy_broker_map).

## Design decisions — 2026-05-30 (trading-app UX + signal engine)

Approved with the operator; building in this order: **A desks-first home →
B Decide revamp → C symbol registry** (B and the desks both lean on C).

### A. Desks-first trading home (`/trader` rebuild) — IN PROGRESS
Treat **each strategy as a "desk" / trader** with its own book, so per-strategy
P&L attribution + reporting is first-class. Constraint: **one strategy → one
broker → one asset class**; attribution key is **(broker × asset-class) →
strategy**, which is 1:1:
- ichimoku_equity → T212 → US Equity
- ichimoku_fx_mr → IG → FX
- intraday_flat → IG → US Equity
Home = portfolio strip (today P&L · equity · cash · action chips) + a row of
**desk cards** (P&L, positions, status, reconcile), click a desk → its
positions/orders/trend. Minimal chrome: connectivity = top-bar traffic light
(done); one money summary (done); warnings/approvals = clickable chips (done).
Analyst cards (charts, scan, trigger, raw order tables, broker-cash detail) →
hideable / drill-in. Caveat: positions + unrealised attribute cleanly (broker
× asset-class); **cash is account-level** (IG shared FX+intraday) → per-desk
"cash" = configured `capital_usd`, not segregated.
SHIPPED so far: connectivity→bar, dup-cash removed, clickable warnings.
NEXT: StrategyDesks cards + default-hide analyst clutter.

### B. Decide page + signal quality — "all WAIT" is by design, and wrong
Today Decide only emits BUY on a **fresh technical crossover today**
(`price_verdict==BUY`); "already trending / no fresh edge" → **WAIT**
(compare.py:384–430). All 5 strategies are the same family (price-vs-MA) +
sentiment/conviction demotion → a wall of WAIT, hardly any BUY/SELL. It answers
"is today a fresh entry?" not "should I own this, short/long term?". Revamp:
1. **Horizon-aware** verdicts — separate short-term (trade) vs long-term
   (invest) columns; a name can be WAIT short-term but ACCUMULATE long-term.
2. **Multi-family signals** via the existing COMPASS composite (momentum +
   quality + valuation + earnings-revision + analyst + sentiment), not just
   price-vs-MA. (Phase-3 / catalyst-overlay gap.)
3. **Richer actions** — BUY / ACCUMULATE / HOLD / TRIM / AVOID, not binary
   fresh-BUY-or-WAIT.

### C. Canonical cross-broker symbol registry (`symbol_map`, verified-only)
Postgres table: canonical, asset_class, currency, yahoo, t212, ig_epic,
verified_at/by. One resolver `resolve(canonical, broker)→broker_id` used by
routing / reconcile / desks; reverse map `broker_id→canonical` for attribution.
IG epics human-VERIFIED only (the `--auto` pick chose a −5X Short SPY ETP /
a PUT / DiaSorin — banned for writes). UI editor + propose-from-search→confirm.
Shared **`us_equity_core`** named watchlist (US subset of ichimoku-equity:
AAPL MSFT NVDA TSLA AMZN GOOGL META BRK-B JPM V) feeds BOTH equity desks;
intraday_flat trades that (not the hardcoded ETFs); UK `.L` excluded (US
session). Needs IG epics for those names (interactive, operator).

### UX + behaviour notes (2026-05-30, second pass)
- **Pre-market staging (CONFIRMED to build):** emit signals as PENDING
  orders always (stage to verify); never *place* into a closed market
  (RiskGate gates placement); **on rerun, cancel this strategy's prior
  PENDING set and restage fresh** (uses /api/admin/oms/bulk-cancel-pending).
  Later: a **config flag to optionally place the pre-signal to the broker**
  pre-market if wished (`place_when_market_closed`, default off).
- **Position-aware signals (principle):** always compute the signal/target
  against the CURRENT position (broker-seeded + optimistic-on-emit) — never
  from assumed-flat — else we emit wrong/duplicate orders. Keep invariant.
- **Equity strategy trust track (ichimoku_equity):** plumbing is sound
  (position-aware seed, correct Ichimoku exit, no runaway) but the EDGE is
  unproven and there's NO risk exit (holds losers until the cloud breaks —
  e.g. TSLA/LIN held while down). Before trusting beyond demo: (1) backtest
  + walk-forward + hit-rate vs buy-and-hold after costs; (2) a configurable
  RISK-EXIT overlay (per-name stop / max-drawdown / regime-flip) on top of
  the Ichimoku exit; (3) eventually multi-family confirmation (ties to B).
- **LLM-in-loop evaluation + transparency (ALL strategies):** every
  strategy's signals must be evaluated by our financial model / LLM quality
  gate, and the gate must LOG its verdict + a human-readable comment
  (good / bad / why — e.g. "vetoed: earnings in 2 days", "boosted: momentum
  + positive sentiment") to the system so we can SEE whether the LLM-in-loop
  is adding value. Ties to: existing LLMSignalGate (llm_gate.py) +
  llm_evaluations table (migration 022). Needs: (a) gate runs for every
  strategy (not just FX/equity), (b) verdict+comment persisted per signal,
  (c) a UI surface (decision-trace / a "LLM gate log") + a scorecard
  (veto/boost counts, and ideally outcome attribution: did vetoed trades
  actually do worse?) to quantify the LLM's contribution.
  **LLM inputs:** overall current position (per strategy/broker) + recent
  market news + sentiment → validate/annotate the signal.
  **Advisory-first:** NO hard gate now — the LLM only LOGS its verdict
  (observe-only) so we can judge if it's adding value; a CONFIG TOGGLE
  promotes it to a hard gate (veto actually blocks the order) once proven.
  Matches trust-before-breadth: prove the LLM-in-loop, then enforce it.
- **Top summary must be cross-broker:** cash + P&L at the top should
  aggregate across strategy/broker (T212 + IG …), not T212-only. Mixed
  currencies (T212 USD, IG GBP) → show per-broker, don't false-sum
  (desks strip to pull /api/integrations/cash-summary).
- **OMS sync ← broker:** make OMS match the broker via audited RECONCILE
  adjustments. (Bug fixed: skip null-ticker T212 rows that violated
  oms_orders.symbol NOT NULL.)
- **Errors should be flagged:** surface backend/operational errors (failed
  sync, strategy abort) in the cockpit alert banner via system_alerts —
  don't fail silently. (Alert infra exists; wire more producers + a
  global-exception → alert hook.)
- **Long-term / Intraday mode flip** adds little value today — keep as
  good-to-have, but only once it genuinely re-parameterises the surfaces
  (default tabs, strategy params, backtest windows) per mode.
- **localStorage gotcha:** changing the cockpit default-hidden list does
  NOT retroactively hide cards for existing users (their `cockpit.hidden`
  persists). Version the key / one-time reset to roll out the calm default.
- Header: removed misleading red "T212 · LIVE" chip (algo trades DEMO);
  name + Sign out grouped; connectivity = top-bar traffic light.
- **Architecture principle (standing):** build MODULAR + service-boundary-
  aware. Production runs MULTIPLE services across DIFFERENT machines — do
  NOT assume same-host (no hard-coded localhost; endpoints/queues config-
  driven; clean Python-signals ↔ .NET-API ↔ Mac-worker ↔ future-services
  boundaries; shared contracts via the API, not in-proc assumptions).
- **TODAY card folded** into the header (status line `todayHeadline`
  inline with the title) + the Strategy desks (P&L/carry-drag on desks).
- **Reconcile bookkeeping orders** (strategy-less + HUMAN, from Sync OMS)
  excluded from the trading feeds ("Orders today by broker" / "Trade
  executed today") — they're not trades. OrdersTable wrapped in overflow-x
  so it can't spill past its card.
- **Symbol click → charts/simulations NOT plugged.** Initial impl was
  reverted (the /symbol deep-dive crashed on a toFixed of a non-number).
  Re-plug properly: either fix the deep-dive crash, or (preferred) an
  INLINE chart/sim panel on click. Currently equity tickers are plain text.
- **Friendly desk names.** Desks show the technical strategy id
  (ichimoku_equity / ichimoku_fx_mr / intraday_flat). Want trader-friendly
  display names (keep the id as the technical key). Proposed (confirm):
  "US Equity Trend" · "G10 FX Fade" · "Intraday (EOD-flat)". Make it a
  display-name map, id stays the key.
- **Strategy catalog (/strategies) screen needs a redesign** — the
  registered-strategies table (name + SCAFFOLD/TRADER/ALPHA tag + status +
  symbol/date/lookback inputs + Run/backtest) is cramped/poorly laid out.
  Rework into clean strategy cards grouped by status (trader/alpha vs
  scaffold), with the run controls tidy.
- **Strategy enable/disable + broker activate-deactivate.** The broker
  mapping editor LIVES ON SETTINGS (StrategyBrokerMapSection) — operator
  found it; it was a discoverability issue (catalog didn't expose/link it).
  Backend: setStrategyStatus/clearStrategyStatus (lifecycle) +
  updateStrategyBrokerMap/deleteStrategyBrokerMap (routing). Improvement:
  in the catalog redesign, surface per-strategy status toggle + a LINK to
  the Settings mapping (or inline it) so it's discoverable in one place.

### Parallel workstreams (other devs — don't clobber)
- **Backtesting + simulation: order-book history from IG (other dev).** A
  separate dev is sourcing **order-book data from IG** to build a proper
  backtesting framework (real fill/spread/depth fidelity vs the current
  bar-close fills). This is the foundation a TRUE options + equity backtest
  needs. Coordinate before touching the simulation / paper-engine fill path
  or the OMS fills schema; the options backtest (P4) will consume it.
- **intraday_flat (IG equity intraday):** merged to main (PRs #28–#33); daemon
  uv-path fixed here. Remaining: interactive IG epic population (operator).

---

## Session log — 2026-05-30: FX duplicate-order bug + multi-broker cockpit

Live work, kept here so nothing is lost (per the "keep updating the
roadmap as we go" rule).

### Shipped (committed to main, deployed AWS + Firebase)
- **Fail-closed position seed (the root bug).** `ichimoku_fx_mr → IG`
  stacked duplicate orders because the per-run position seed read the
  broker (golden source) but, on ANY failure (IG `/positions` timeout),
  silently fell back to a *flat* book and re-fired a full entry. Now:
  a `--push` session against a *real* broker ABORTS (no orders) if it
  can't confirm position — for **every strategy and every broker**
  (sim brokers exempt; unknown brokers treated as real by default).
  `paper_session.py` `PositionSeedError` / `broker_requires_position_seed`.
- **Operational alerts.** New `system_alerts` table (migration 027) +
  `IAlertStore` + `POST /api/ingest/alert` + `GET /api/alerts`. The
  abort is logged AND raised as a cockpit alert (dedup'd). New
  `AlertBanner` on `/trader`.
- **Cockpit reorg.** Positions + trend are the main top section;
  connectivity collapsed to a **traffic-light strip** (dot per service,
  click amber/red for detail).
- **Multi-broker reconciled positions.** `PositionsPanel` segregated
  **by broker → product**, split into **two side-by-side cards
  (Equity | FX)**. Each broker account reconciled vs OMS (broker is
  golden, OMS audit-only); per-symbol **drift highlighted** (⚠), FX
  **net-by-pair** under stacked deals.
- **Flatten FX.** `IGClient.CloseDealAsync` + `POST
  /api/integrations/ig/positions/flatten` (all or one symbol) + UI
  "Flatten all FX" / per-deal close, behind a confirm. Undoes the
  stacked duplicates.
- **Orders by broker.** `OrdersByBrokerPanel` — today's flow grouped by
  broker incl. CANCELLED/REJECTED (previously invisible), with reasons;
  Broker column + readable symbols (`util/brokerSymbols.ts`) in
  `OrdersTable`. Confirmed live OMS holds 154 IG + 46 T212 orders.
- **Clickable equity tickers** → `/symbol/<ticker>` deep-dive for trend.
- **FX market-hours guard** — `ichimoku_fx_mr` no longer fires when spot
  FX is closed (weekend); `_fx_market_open()` (24/5: closed Sat, Sun
  pre-21:00 UTC, Fri post-21:00). Stops the `MARKET_CLOSED` spam at
  source. "Don't send orders into a closed market."
- **Truthful flatten** — IG flatten now CONFIRMS each close, so it
  reports real closed-vs-rejected (weekend → "rejected: MARKET_CLOSED")
  instead of a false "Closed 12/12".
- **Per-deal close** — flatten accepts a `dealId`; per-row close closes
  THAT deal (was flattening the whole pair).
- **Positions UX** — Equity + FX are full-width stacked cards; FX leads
  with net-per-pair and collapses individual deals behind an expander.
- **Order time clarity** — non-today orders show date + age (a stale
  SUBMITTED-from-last-night can't look fresh).

### Open queue (next)
- **Nav bar polish** — the "More ▾" overflow overlaps the long-term/
  Intraday toggle (squashed); the "T212 · LIVE · EQUITY ONLY" badge is
  bulky / low-value — shrink or drop. (Layout.tsx)
- **Stale SUBMITTED orders** — last night's `ichimoku_equity` orders
  (20:47 UTC, after US close) sit in SUBMITTED forever; fills aren't
  being recorded back (T212 demo fill poller?) — investigate.
- **Equity market-hours guard** — same as FX: don't fire equity orders
  after the US/LSE session close (the 20:47 after-hours batch).
- **"0 bars" diagnostic copy** — say "market closed / no data" instead
  of "source feed misconfigured" when the venue is shut.
- **OMS sync-from-broker** (UI button wired but `onSyncOms` not passed
  yet): when OMS=0 and broker has a position, a button to **overwrite
  OMS from the broker** (golden source). Needs a backend reconcile-WRITE
  endpoint (generalise the T212-only `PositionReconciler` to all
  brokers). The live T212 equity book shows ⚠ drift on every symbol —
  OMS isn't recording the equity fills — so this is needed.
- **Inline trend chart on ticker click** (current: links out to
  `/symbol`). Want the chart inline in the cockpit.
- **Other asset classes** — Options (planned: trader "we will have
  them"), Futures, Crypto. `productOf()` already returns an extensible
  union; positions view groups by product so a new class = a new card.
- **Weekend FX data gap.** IG offers weekend FX/CFD markets but the bar
  feed (Yahoo) has no weekend FX bars → `ichimoku_fx_mr` sees 0 bars
  Sat/Sun and can't act. Also the "0 bars" diagnostic copy ("source
  misconfigured") is misleading — should say "market closed / no data".
- **Mini-lot round-trip** — IG MINI FX positions report fractional
  mini-lots; the seed's `int(float(qty))` truncates sub-1.0 lots to 0
  (re-fire risk even when the seed succeeds). Make units↔mini-lot
  conversion symmetric.
- **Stale repo plist** — `scripts/launchd/com.tradepro.paper-fx.plist`
  says `--broker t212`; installed job is `--broker ig … auto`. Reconcile.
- **LiveSignalFeed** — prettify IG epics (uses `CS.D.EURUSD.MINI.I…`).

---

## Trustworthy data layer — north-star enabler

**Status:** Phase A shipped (PR #34 / commit `6419237`). Subsequent
phases queued. Single-line framing: **TradePro cannot honestly claim
a track record for its intraday strategies until this layer ships.**
See [`CURRENT_BACKTEST_LIMITATIONS.md`](CURRENT_BACKTEST_LIMITATIONS.md)
for the audit of what is and isn't trustworthy today.

### Coordination with parallel work (2026-05-30)

The 2026-05-30 design-decisions block introduces two adjacent pieces
that this roadmap consumes:

- **Plan C — canonical `symbol_map` table** (verified-only, IG epics
  human-curated, `us_equity_core` shared watchlist). Every data
  operation here keys on `(canonical, asset_class)` from `symbol_map`,
  not on broker-native ids. Backfilling SPY means "backfill canonical
  SPY"; the cache layer fans out to whichever providers the
  preferences table prescribes for `(us_equity, 1m)`.
- **Parallel order-book history workstream** (other dev). Their work
  improves fill realism by persisting order-book history. **This
  roadmap does not touch the OMS fills schema, the simulation fill
  path, or the paper-engine fill timing.** Phase F (fill-quality
  bid/ask capture) coordinates with their schema before landing —
  rather than duplicate, we extend their additions with the L1
  snapshot field if it isn't already there.

Result: this workstream stays in its lane (bar cache + provider chain
+ assumption registry + UI-triggerable ops) and dovetails with theirs
when their fill-history additions land.

### Why this is north-star

The project goal — *BUY/SELL/HOLD recommendation across ETF universe,
evidenced by backtest + stress-scenario impact* — has two halves. The
recommendation half (strategies + LLM gate + risk module) is shipped.
The **evidenced by backtest + stress-scenario** half is materially
incomplete: daily strategies have defensible backtest evidence;
intraday strategies have **none beyond the last 7 days** because
free-tier 1-minute data caps at that. The roadmap below closes that
gap progressively, with operator visibility from day 1.

### Design principles (non-negotiable)

1. **Asset-class-pluggable**. Adding a new asset class (options,
   futures, crypto, Indian equity for the future sleeve) is a single
   file (`data/asset_classes/<class>.py`), never a schema migration
   or core refactor.
2. **Partial data fails loud**. A backtest that asked for SPY 2024 1m
   bars either gets complete data or a structured error. Silent
   partial reads are a banned behaviour.
3. **Provider preferences are operator-editable** per (asset_class ×
   resolution). New providers enter the chain without code change.
4. **Observability + error diagnostics are first-class**. Every fetch
   emits structured telemetry (provider, latency, rows expected vs
   returned, error class). Health endpoint surfaces gaps per symbol.
5. **Assumption registry**. Every assumption the system makes about
   data (no slippage modelled, LLM gate anachronistic in backtests,
   etc.) lives in `data_assumptions` table, editable, surfaced in UI.
   A future investor reading the cockpit can see what's PARTIAL /
   OPTIMISTIC / FICTIONAL today.
6. **Reproducibility hash**. Every backtest result stamps the data
   state (provider, version, partition hashes). Two runs with the
   same hash MUST produce identical numbers.
7. **Every data operation is UI-triggerable**. Trade support / non-
   engineer operators can initiate backfills, reloads, validations,
   repartitions from the Settings panel. No SSH / CLI required for
   routine ops. Audit (who / when / why) lands on every action.
   See "Operational model" below for the architecture.
8. **Canonical-symbol identity**. Data ops key on `(canonical,
   asset_class)` from the `symbol_map` table (Plan C of the
   2026-05-30 design block). Broker-native ids (T212 tickers, IG
   epics) are looked up at fetch time, never stored in the cache
   manifest. Symbol renames / cross-broker drift cannot orphan
   cached partitions.

### Operational model — UI-triggerable data ops

**Substrate**: the existing `session_requests` table + `/api/ops/*`
endpoint family (migrations 007 + ongoing). Pattern is identical to
the way intraday + paper sessions work today:

```
   UI                   API (.NET)                Worker (Python)
   ─────                ──────────                ────────────────
   POST /api/ops/       INSERT session_           POLL /api/ops/
   run-data-{kind} ──→  requests row              poll-data ──→ atomic
   {payload}            kind = data_*             UPDATE-RETURNING
                        state = Pending           one Pending row
                                                  → Claimed

                                                  do the work

                                                  POST /api/ops/
                                                  complete-data/{id} ──→
                                                  → Completed | Failed
                        UI shows status via
                        GET /api/ops/sessions
                        ?kind=data_*
```

**No new queue, no new worker process model.** Reuses what
`tradepro-intraday-engine` and `tradepro-paper-watch` already do.
A new `tradepro-data-worker` daemon polls for `data_*` kind sessions
and routes to handlers (backfill / reload / validate). Launchd plist
mirrors the existing patterns.

**Registered op kinds** (planned, formalised in Phase B):

| kind | What it does | Payload | Typical caller |
|---|---|---|---|
| `data_backfill` | Pull historical bars for a (symbol, resolution) over a date range | `{canonical, asset_class, resolution, from, to, force?}` | Trade support populating a new symbol; recovering from a failed daily incremental |
| `data_reload` | Force re-fetch + overwrite an existing partition (e.g. after a corp action) | `{canonical, asset_class, resolution, partition}` | Trade support correcting a known drift |
| `data_validate` | Walk manifests for a symbol, report gaps + integrity violations | `{canonical, asset_class}` | Trade support before trusting a backtest |
| `data_repartition` | Rewrite a Parquet partition (e.g. schema migration) | `{canonical, asset_class, resolution, from_version, to_version}` | Phase D schema upgrades |
| `data_purge` | Remove a partition (operator decision; logged loudly) | `{canonical, asset_class, resolution, partition, reason}` | Trade support cleaning up after a provider revision is known-bad |

**UI surface** (per phase):

- **Phase B**: Each row in the Data Health panel gains a "Validate"
  button (kind=`data_validate`) — non-destructive, surfaces gaps.
- **Phase C**: Per-row "Backfill missing" + "Reload" buttons
  (kind=`data_backfill` / `data_reload`). Confirm prompts with
  payload preview. Job status updates in-place via polling
  `/api/ops/sessions?kind=data_*`.
- **Phase D**: Coverage matrix cells become click-to-backfill.

**Audit + safety**:
- Every operation records `requested_by` (auth context) + `params`
  + `reason` (operator-supplied for destructive ops).
- Destructive ops (`data_reload`, `data_purge`) require explicit
  reason text + UI confirm modal that shows what gets overwritten.
- Result summary on `Completed` row includes rows-written +
  provider-used + partition-hash so the cockpit can show "Yes,
  data is now there" rather than just "job done".
- Failed ops surface error + retry-strategy hint
  (`retry_strategy = "switch_provider" | "exponential_backoff" |
  "user_intervention" | "fatal"`).

**No standalone load process**. The `tradepro-data-worker` daemon
lives next to the existing `tradepro-paper-watch` /
`tradepro-intraday-engine` daemons on the Mac, using the same
launchd + poll + claim pattern. One worker process, multi-handler.
If we ever need to fan out heavy backfills, an `instance_id` field
on the worker poll lets us scale to N workers picking different
rows from the same queue — no new schema, no new infra.

### Phases

**Phase A — Foundation: visibility + framework (this PR)**
- `CURRENT_BACKTEST_LIMITATIONS.md` — trader-readable limitations doc.
- `data_source_preferences` table — operator-editable provider chain
  per (asset_class × resolution).
- `data_assumptions` table — auditable list of system assumptions
  about data quality, with status + severity + remedy.
- Backend endpoints (GET/PUT preferences; GET assumptions + limitations).
- Backend endpoint placeholder for backfill trigger
  ("Phase C — not yet implemented").
- Settings → Data Health & Provider Preferences panel that surfaces
  all of the above + clearly badges what's stub vs functional.
- STRATEGIES.md per-strategy limitation banners linking to the doc.

**Phase B — Asset-class-pluggable bar cache**

*Phase B-1 SHIPPED in this PR.* Subsequent slices:

- **B-1 (this PR)**:
  - `BarStore` orchestrator with atomic writes + manifest validation
  - First asset-class plugin: `us_etf`
  - First provider: `yfinance` (with injectable fetch_fn for tests)
  - Migrations 031 (`bar_cache_events`) + 032 (`bar_cache_health`)
  - Telemetry sink (DB + JSONL fallback)
  - Operator CLI: `tradepro-bar-cache-get`
  - 9 BDD scenarios covering happy path + failure modes
  - Hardcoded chain `["yfinance"]` (DB-driven chain in B-3)
- **B-2 SHIPPED (PR #37 / 5d6dc3e)**: telemetry visibility loop. POST
  `/api/admin/data-trust/bar-cache/events` + GET `/events` + `/health`
  endpoints; `BackendTelemetrySink` POSTs every fetch; cockpit's
  "Bar cache activity" panel renders coverage + recent events.
- **B-3 SHIPPED (this PR)**: provider chain driven from
  `data_source_preferences` table. `PreferencesLoader` GETs the
  preferences endpoint with a 60s TTL cache; `BarStore` resolves the
  chain per (asset_class, resolution) per call. Loader is non-fatal
  — HTTP failure or missing row falls back to the BarStore default
  chain. Telemetry breadcrumb (`chain_source: preferences|default`)
  on every fetch. Manifest records the actually-resolved chain.
- **B-4**: additional plugins (`us_equity`, `fx_spot`) + additional
  providers (`ig` via /prices, `finnhub`). With B-3 shipped, an
  operator adds a provider to the chain via the Settings panel and
  it's live on the next fetch — no code change at the preferences
  layer is required.
- **B-5** (was B-2 in the original plan): strategy opt-in.
  `intraday_flat` consumes BarStore via a feature flag; backward-
  compatible with existing `cache.py`. Deferred until B-4 ships
  multi-provider coverage so the opt-in actually buys something.

**Phase C — Operator-facing backfill (UI-FIRST)**
- New `tradepro-data-worker` daemon polls `session_requests` for
  kind `data_*` (mirrors `tradepro-intraday-engine` pattern).
- POST `/api/ops/run-data-backfill` enqueues with kind=`data_backfill`.
  POST `/api/ops/run-data-reload`, `/run-data-validate`,
  `/run-data-purge` for the other registered kinds.
- POST `/api/ops/poll-data` (worker), POST
  `/api/ops/complete-data/{id}` (worker callback). Mirrors the
  intraday + paper poll/complete pair exactly.
- UI in the Data Health panel: "Validate", "Backfill missing",
  "Reload" buttons per row. Confirm prompts with payload preview.
  Job status updates in-place via GET `/api/ops/sessions?kind=data_*`.
- CLI parity: `tradepro-backfill-bars --canonical SPY --asset us_etf
  --resolution 1m --from 2024-01-01 --to today` for power-users.
- Rate-limit guard (token bucket per provider) — never breaches limits.
- Audit: every enqueue records `requested_by` + `params` + `reason`
  (mandatory for destructive ops).
- Launchd plist: `com.tradepro.data-worker.plist` installed by
  `install-launchd.sh --data-worker` (opt-in flag, like `--intraday`).

**Phase D — Reproducibility + audit**
- Backtest results stamp `data_provider`, `provider_version`,
  `bar_count_per_symbol`, `bar_partition_hash`.
- Result viewer surfaces the data state ("ran on cache hash abc123;
  matches the 2024-08-15 baseline").
- Walk-forward + Monte Carlo become fully reproducible.

**Phase E — Backtest hard-block on incomplete data**
- The backtest CLI / endpoint refuses to run when the data layer
  detects gaps in the requested range.
- Refusal includes a specific remediation message (which provider to
  configure, which CLI to run, or `--allow-partial` to override —
  override is opt-in and stamped on the result).

**Phase F — Fill-quality + slippage layer**
- Store IG L1 bid/ask snapshot at every fill (`oms_fills` extension).
- Build an empirical slippage model from realised vs theoretical fills.
- Spread-haircut backtest mode (apply ½-spread or full-spread cost
  per round-trip).

**Phase G — Coverage matrix UI**
- Grid view: rows = (asset_class, symbol), columns = months. Cells
  colour-coded full / partial / missing / unknown. Click → drill into
  the specific gap with explanation + fix path.

**Phase H — LLM gate replay**
- Backtest mode for the LLM gate replays stored `llm_evaluations`
  rows by timestamp — honest from the date capture began
  (~ 2026-05-28).
- Empirical measurement of gate contribution: cohort analyses
  splitting "backtest with gate disabled" vs "live with gate active".

**Phase I — S3 hybrid storage**
- Local Parquet = hot working set; S3 IA = warm archive; S3 Glacier
  Deep Archive = cold archive.
- Daily incremental sync local → S3.
- Bootstrap restore CLI ("rehydrate cache from S3").
- EC2 same-region read for full-universe backtests.

**Phase J — Additional asset classes (mandate-driven, not now)**
- Options chains EOD snapshots (when an options strategy joins the
  roadmap).
- Futures with contract rolling (when needed).
- Crypto (BinanceProvider.cs exists but no consumer today).

### Coordination

Phase A lands as a single PR. Subsequent phases are independent and
can be sequenced or parallelised by need. Each phase produces visible
operator value (a panel, a button, an error message) — no "platform
work for platform's sake".

---

## Risk module — pre-trade checks, position sizing, kill switches

**Status:** PLANNED (foundational — every algo platform needs this
before live capital). Sized into phases so we ship the highest-value
gates first and the trader gets visible safety as it lands.

**Why:** today the OMS accepts any intent the strategy emits and the
trader manually decides Approve / Reject. Live trading needs hard
gates that *block* an order before it hits the broker — not just an
amber warning afterwards. A bug in a strategy that re-fires a 1000-
share order every tick would bankrupt the account before a human
notices. The kill switches sit upstream of OMS approve and downstream
of strategy emit so they apply uniformly to manual + auto modes.

### Where it plugs in
```
strategy.on_bar → Order   ┐
                          ▼
                   ┌──────────────┐
                   │ RiskModule   │  ◄── block / size / annotate
                   │  - pre-trade │
                   │  - sizing    │
                   │  - kill sw.  │
                   └──────┬───────┘
                          ▼
                   OMS.enqueue (PENDING_APPROVAL or REJECTED-by-risk)
                          ▼
                   Broker placement
```
Settings live in `app_settings_kv` so the trader tunes every limit
from /settings without a code change.

### Phase 1 — pre-trade gate + hard limits (foundational)
- **Order size cap** per symbol (notional + share count)
- **Order velocity** — max N orders per strategy per minute (anti-runaway)
- **Cash availability** check against T212 free balance (block when
  order cost > free * safety_margin)
- **Symbol blacklist** — DB-backed (operator can flag specific tickers
  off-limits, e.g. illiquid ETFs)
- **Risk events table** — every block / sizing-adjustment recorded
  with reason so the trader can audit "why didn't this fire?"
- **/risk page** — read-only board of today's events + current cap
  utilisation.

### Phase 2 — position sizing engines
- **Risk-per-trade** (% of capital, stop-distance aware) — replaces
  the strategy's bare qty when configured.
- **Vol-targeted** — already in ichimoku_equity; lift into a shared
  RiskModule so other strategies can opt in without re-implementing.
- **Kelly / fractional-Kelly** for high-confidence signals.

### Phase 3 — exits + stops
- **Mandatory stop on every entry** (intraday rule from memory — see
  [intraday_exit_framework]) — RiskModule rejects entries without
  a stop attached.
- **Time-based exit** — flatten at 15-min-before-close for intraday.
- **ATR-trailing stop** as a strategy-attached option.

### Phase 4 — portfolio-level
- **Sector exposure cap** — % of portfolio per GICS sector (we have
  this metadata from the universe scraper).
- **Correlation-aware sizing** — size down when adding a position
  correlated with existing holdings.
- **Beta / currency exposure** rolled up per request.

### Phase 5 — kill switches + circuit breakers
- **Daily loss limit** — when realised + unrealised loss exceeds
  threshold, auto-flip OMS to Manual mode + page the operator. No
  new entries until reset.
- **Per-strategy circuit breaker** — strategy X breaches its budget
  → strategy X auto-disabled, others keep running.
- **Connection-loss handler** — Mac daemon drops T212 for >N
  minutes → auto-cancel open orders + flag.
- **Stale-signal detector** — strategy hasn't emitted a decision in
  M minutes when it should have → mark as down.

### Phase 6 — risk reporting + stress
- **Daily risk report** (one-pager email at session close): max DD,
  win rate, fill quality, current exposures, cap utilisation.
- **Stress scenarios** — replay 2008 / COVID / flash-crash bar data
  against current positions to estimate worst-case loss.
- **VaR / CVaR** with rolling 60-day distribution.

### Implementation principles
- **Fail-closed**: when the risk module can't evaluate a check (DB
  down, T212 unreachable), block the order. Better to miss a trade
  than to leak capital.
- **Settings, not constants**: every threshold lives in
  app_settings_kv keyed under category "Risk" so the trader tunes
  without redeploys.
- **Audit everything**: a `risk_events` table records every block /
  size-adjustment / kill-switch trip with timestamp + actor +
  reason. Operator can grep it without re-running scenarios.
- **Default to safe**: each new limit ships with a conservative
  default that the trader can loosen later as they build trust.

### Test discipline
- xUnit on every gate (block, size, kill-switch transition).
- Behave on the strategy → RiskModule → OMS chain.
- Synthetic broker (mock T212 + IBKR) for fault-injection tests:
  what happens when cash check 500s, when order velocity hits the
  cap mid-batch, when the daily-loss switch trips during a
  partial fill.

---

## ichimoku_fx_mr v2 — multi-indicator intraday FX MR

**Status:** PLANNED (user feedback 2026-05-26 — discuss with quant first).

**Why:** v1 uses Ichimoku as the sole signal source for hourly FX
mean-reversion. Ichimoku is a *trend-confirmation* tool tuned for
daily Japanese equity bars; using it for intraday FX MR is contrarian
to its design and breaks down badly in trending regimes. The hourly
26-bar displacement also means the cloud lags real price by 26h —
by then the MR opportunity has often already closed. Single-
indicator strategies are fragile to regime shifts.

**v2 design (proposed):**

| Layer            | Indicator                          | Role |
|------------------|------------------------------------|------|
| Primary trigger  | Bollinger Band(20) z-score \|z\| > 2 | Fade extremes |
| Confirmation     | RSI(14) > 70 / < 30                | Avoid false MR |
| Vol regime gate  | ATR(14) z-score                    | Skip when vol exploding (trend regime) |
| Session filter   | London/NY overlap (12:00-16:00 UTC)| FX MR works best in overlap |
| Demotion only    | Ichimoku cloud                     | Don't fade if price firmly outside cloud (that's trend) |
| Stop / size      | ATR-based                          | Vol-adjusted, not fixed |

**Future v3:** EUR/USD vs GBP/USD cointegration pairs trade for the
highest-confidence setups. Ornstein-Uhlenbeck fits with deviation
thresholds.

**Demo plan:** ship v1 with `caveats` banner surfaced in the cockpit
Trigger panel (done 2026-05-26) + the strategy_optimisation_frequency
default of 240 minutes (every 4h) so v1 doesn't burn data-provider
quota while the quant reviews v2 design.

---

## Portfolio safety — default to T212 DEMO

**Status:** PLANNED (user request 2026-05-25).

**Problem:** the Portfolio page currently reads from the live T212 account
(`Trading212Client` → `/equity/positions`). On a fresh sign-in or accidental
"Run" click, real-money positions are visible by default — and worse, any
action button can hit the live account.

**Fix:**
1. Default `Trading212Mode` to `demo` everywhere — Portfolio reads, paper
   approvals, MCP `get_portfolio`. The `Trading212DemoClient` already exists
   (sibling type to enforce "this code path can never hit live").
2. Settings page gains an explicit `Account: DEMO / LIVE` pill at the very
   top — red border + confirmation modal when toggling to LIVE.
3. Visual: every page header shows a small `LIVE` chip when live mode is
   on (red bg). Demo shows a neutral `DEMO` chip so the user always knows.
4. Auth lock: only the owner UID (sunnylnct007@gmail.com) can flip to LIVE.

**Tickets:**
- T#P1 Portfolio backend: route reads through `IT212Client` interface that
  resolves to demo or live by `Settings.Trading212Mode`
- T#P2 Frontend mode chip + confirmation modal on toggle
- T#P3 Owner-UID gate on the toggle endpoint

---

## Strategy playground — synthetic-input simulator

**Status:** PLANNED (user request 2026-05-25).

**Why:** When a strategy fails to trade in paper (e.g. `ichimoku_fx_mr`
returning signal=0 with 1000+ bars), we currently have no way to ask "what
would the strategy do if I fed it X?" The fix today is to write throwaway
Python smokes, which is invisible to non-coders and not exportable. A UI
sandbox closes that loop: pick a strategy, paste/upload OHLCV bars (or
synthetic patterns like trend / chop / breakout), see decisions + orders +
P&L without touching the daemon.

**Approach:**
1. New `/playground` page. Strategy picker (reuses paper-strategies catalog).
2. Bar input: paste CSV, upload file, OR pick a preset (trend up / range /
   breakout / NFP day from history). Validates min-bar count vs strategy
   warmup gate; warns if insufficient.
3. Backend `/api/playground/run` POSTs to a Python sidecar that runs the
   strategy through an in-memory `ReplayBarBus` + `PaperOrderRouter`,
   returning the same `paper-snapshot` shape PaperLive already renders.
4. Renders the same Why panel + Export JSON UI the PaperLive page uses,
   so the investigation toolchain stays consistent.
5. Bonus: save a playground run as a "fixture" attached to a strategy so
   the next time someone touches that strategy code, they can re-run the
   fixture and diff the decisions (regression-test ergonomics for
   non-coders).

**Tickets:**
- T#SP1 `/api/playground/run` sidecar endpoint
- T#SP2 `/playground` page with strategy + bar-source picker
- T#SP3 Bar presets library (trend / chop / breakout / NFP-day)
- T#SP4 Reuse PaperLive Why panel + Export JSON
- T#SP5 Fixture save + diff against committed baseline

---

## IBKR integration — paper-first

**Status:** PLANNED (user request 2026-05-25). **Now also subsumes
the T212 CFD gap** — see "T212 CFD limitations" note below.

**Why:** T212 covers equities + ETFs + some FX but no options, futures, or
extended-hours. IBKR is the eventual home for the algo + quant strategies
that need richer instrument coverage. Same paper-first principle as T212:
start with paper trading, prove the wiring, switch to live by toggle.

**T212 CFD limitations (2026-05-26):**
T212 publishes a public API for its Invest product (`/api/v0/equity/*`)
but does NOT expose CFD via public API. The FX strategy
(ichimoku_fx_mr) needs CFD to actually trade FX since T212 Invest
doesn't carry FX instruments. Consequences today:
  - CFD cash + positions are invisible from TradePro (user's
    £50k demo CFD balance can't be fetched).
  - FX strategy intents land in OMS, can be approved on /oms, but
    .NET's Trading212DemoClient.PlaceMarketOrderAsync fails because
    `/equity/orders/market` doesn't know about EURUSD as an
    instrument.
  - Trader can still VALIDATE FX signals via the cockpit's "Strategy
    signals" widget (commit 84f19c8) which surfaces fire-buy /
    fire-sell decisions without execution.
IBKR handles FX natively → IBKR integration replaces this gap
entirely. No point building a parallel T212-CFD scrape path.

**Approach:**
1. `IBKRClient` mirroring the `Trading212Client` shape so the OMS service
   sees a single `IBroker` interface — same `IBrokerOrder` /
   `IBrokerFill` types. New `oms_orders.broker = 'IBKR_PAPER' | 'IBKR_LIVE'`.
2. Connect via IB Gateway (TWS API) running on Mac initially. AWS-side
   batch execution comes later via `ib-insync` or `ibapi` containerised.
3. Paper account credentials go into `tradepro/all` Secrets Manager under
   `ibkr-paper-username` / `ibkr-paper-password` (same pattern as T212).
4. Strategies page Run button gains a `Broker: T212 / IBKR` picker.
5. Portfolio page gains an IBKR positions panel alongside T212.

**Tickets:**
- T#I1 IBKRClient with paper-mode connect + positions / orders / fills
- T#I2 IBroker abstraction over T212Client + IBKRClient
- T#I3 OMS broker enum extension + migration
- T#I4 Strategies page broker picker
- T#I5 Portfolio page IBKR positions panel

---

## Order Management System (OMS) — proper persistence + lifecycle

**Status:** PLANNED. Started ad-hoc placement (Trading 212 pending-orders queue
+ /api/orders log). For algorithmic trading we need a proper OMS so every order
the platform ever placed — manual, paper, algo, multi-leg — is queryable in one
place with a complete state-machine trail.

**Why now:** as Track 1 (intraday algo), Lane A (quant-engine systematic
strategies), and Track 2 (Compounder allocation tactics) all start placing
orders programmatically, the existing `pending_orders` table alone won't scale:
no strategy_id linkage, no parent/child orders for brackets, no cancel-on-mode-
switch hook, no fill-by-fill reconciliation, no broker_request_id idempotency.

**OMS design sketch (to be expanded in a dedicated spec):**

1. **`oms_orders`** — canonical lifecycle row per order, append-only state changes
   via `oms_order_events`. Columns:
   - `id` (uuid, pk)
   - `client_order_id` (uuid, unique — our idempotency key)
   - `broker` (`T212_DEMO` / `T212_LIVE` / `IBKR` / `PAPER`)
   - `broker_order_id` (nullable; populated post-acknowledgement)
   - `parent_order_id` (nullable — bracket / OCO grouping)
   - `strategy_id` (nullable for manual orders; FK to paper_strategies)
   - `signal_id` (nullable; FK to signal_ledger entry that fired it)
   - `decision_id` (FK to `decisions` — the mode/policy state that authorised it)
   - `symbol`, `side` (BUY/SELL), `qty`, `order_type` (MKT/LMT/STP/STP_LMT)
   - `limit_price`, `stop_price`, `time_in_force` (DAY/GTC/IOC)
   - `state` (`PENDING_APPROVAL` / `SENT` / `WORKING` / `PARTIAL` / `FILLED`
     / `CANCELLED` / `REJECTED` / `EXPIRED`)
   - `placed_by` (`HUMAN` / `STRATEGY_AUTO`)
   - `created_at_utc`, `last_state_change_at_utc`
   - `cancelled_reason` (text — `USER_FLIP_TO_MANUAL` / `STRATEGY_KILL_SWITCH`
     / `RISK_LIMIT` / `BROKER_REJECT`)

2. **`oms_order_events`** — append-only state-machine log. Every state change
   writes one row with the prior + new state, the broker payload that triggered
   it, and the request/response IDs. Reconstruct the full history of any order
   from a single SQL filter.

3. **`oms_fills`** — per-fill rows (an order can fill in multiple chunks).
   Columns: order_id, fill_id, qty, price, fee, currency, fill_at_utc,
   broker_fill_id.

4. **`decisions`** — policy/mode state that orders point at. Every Settings
   change (placement mode flip, kill-switch toggle, risk limit change) writes
   a row here. When a decision flips manual → auto or auto → manual, a worker
   walks the cascade table to cancel/leave-alone working orders per policy.

5. **OMS service layer** — single `IOmsService` interface that ALL placement
   paths go through (Settings approve, intraday engine auto-place, MCP-driven
   one-off). No more `pending_orders` direct writes outside the service. The
   service owns:
   - idempotency check on `client_order_id`
   - state-machine transitions (illegal transitions raise)
   - cascade actions on mode flip
   - retry/resync against broker (poll for state changes the broker didn't push)

6. **Migration plan** — existing `pending_orders` becomes a projection /
   compatibility view over `oms_orders`. The Mac engine + .NET API switch
   to the new service one path at a time; the old table stays writable
   during transition to keep the daily plist running.

**Tickets to write:**
- T#A OMS schema + migration (oms_orders, oms_order_events, oms_fills, decisions)
- T#B IOmsService implementation + idempotency
- T#C Mode-flip cascade worker — kill working orders when user goes manual
- T#D Wire intraday engine + paper-pending-order ingest through IOmsService
- T#E UI: orders table on /portfolio + filter by strategy / state / broker
- T#F Reconciliation worker — poll broker for state changes the API missed

---

## Recently shipped (May 2026)

Tracks meaningful work that's already in `main` so this doc stops drifting
out of date. Each entry is one line: what changed and why it mattered.

**2026-05-28 → 2026-05-29 — Trader-trust + OMS hygiene marathon:**

The visible "I can't trust this thing to trade for me" problems all
landed in one stretch — strategy → broker → fill chain is now proven
working end-to-end for both equity (T212 demo) and FX (IG demo).

**The architectural unlock — broker is the golden source.** Locked in
memory ([project_broker_is_golden_source]). OMS is for audit /
reporting only; never trusted as truth for position decisions. Every
position-aware call now queries the broker's `/positions` endpoint
first (T212 `/equity/positions`, new IG `/api/integrations/ig/positions`).

**Position-awareness — the NVDA SELL story.** Strategy was firing
mechanical BUY signals on held longs and rejecting legitimate SELL
exits as "short_disallowed". Two-level fix:
- `paper_session._seed_strategy_positions_from_oms` queries broker,
  passes to `strategy.seed_positions()` AND `engine.ledger.seed_positions()`.
- Base `Strategy.seed_positions` now populates `self.positions` (the
  canonical dict the risk gate reads) — subclasses call `super()`.
- Fractional broker qty truncated toward zero (T212 holds 6.7022 NVDA →
  strategy sees 6, never tries to sell more than owned).
- Verified live: NVDA SELL 6 → T212 position dropped 6.7022 → 0.7022.

**IG end-to-end proven.** `IGClient.SearchMarketsAsync` + admin
`/ig/smoke-order` + `IGOmsFillPoller` (clones T212 pattern but talks
`/confirms/{dealRef}`) + mini-lot conversion (1 mini = 10K base units,
qty rounded to 1 decimal). FX cap raised $10K → $100K (mini lots are
larger than equity shares). Verified: `ichimoku_fx_mr SELL 0.9 EURUSD
MINI → FILLED at IG_DEMO` on a real strategy firing.

**E2E smoke test.** `strategies/scripts/smoke-e2e-paper-trading.sh`
exercises both broker chains in <90s — preflight, IG enqueue, IG
fill, IG audit, T212 enqueue, T212 broker-order-id, T212 hours-aware
terminal state. 8/8 GREEN. Run before claiming any chain "fixed".

**OMS hygiene.** Multiple fixes that turn OMS from "noisy and lies"
into "audit-trustworthy":
- T212 fill poller: 404-on-hot-cache now assumes FILLED (empirically:
  broker_order_id only issues post-acceptance). Heal endpoint flipped
  58 historical false-cancellations to FILLED → state histogram
  93 FILLED · 265 CANCELLED · 4 REJECTED (was 35 FILLED with many
  orphans before the heal).
- In-flight dedupe in `PostgresOmsService.EnqueueAsync` — same
  (broker, strategy, symbol, side) with non-terminal status in 15 min
  returns existing row instead of new (kickstart-spam 15 → 1 row).
- `oms_orders.broker` CHECK constraint extended with IG_DEMO/IG_LIVE
  (was rejecting IG enqueues at migration boundary).
- Universal OMS path: T212OrderRouter auto-mode now routes through
  `/api/oms/orders` instead of T212 direct call, so OMS sees every
  trade (`broker_label_override` for the IG paper profile).
- Order-tied + standalone LLM evaluations endpoint (#63 wired —
  sentiment_score CLI posts to `/api/llm-evaluations` every call).
- `RiskMonitorService` auto-sweeps stale PENDING_APPROVAL >2h with
  reason `stale_pending_auto_clean` so OMS never holds forgotten
  intents that re-execute when conditions changed.

**Connectivity / settings infrastructure:**
- AWS Secrets Manager bundle fetch fixed (was eu-north-1 default,
  needed eu-west-2). `tradepro/ig` secret loaded independently of the
  primary `tradepro/all` bundle. Linux EC2 IMDS hop limit bumped to 2.
- `PostgresOmsModeService` replaces the in-memory impl so OMS mode
  (Auto / Manual) survives container restarts — was silently reverting
  to Manual on every redeploy.
- LLM probe reads `llm_url` + `llm_model` from `app_settings_kv`
  directly (operator-tunable) and renders as `disabled` with note
  "runs on Mac worker, unreachable from EC2 by design" when the URL
  is a localhost/internal address. Stops false-down alarms.
- `/health/integrations` extended with IG status + DB latency + T212
  demo cash probe.

**Cockpit surfaces** (the "visual legitimacy check" set):
- `BrokerCashStrip` — every broker's free / total in one row.
- `ConnectivityPanel` — at-a-glance broker / LLM / DB status.
- `PositionChartsCard` — top 5 held positions overlaid with strategy's
  Ichimoku cloud charts. Entry markers (avg_price horizontal line +
  "long N @ X" annotation) so the trader sees where we bought
  relative to trend / cloud / indicators.
- `LiveSignalFeed` — chronological feed showing SIGNAL → ORDER →
  FILL events in real time (10s poll). Closes "I haven't seen a
  proper signal flowing yet" gap.
- `NewsContextPanel` on Symbol Deep Dive — rolling LLM sentiment
  scores per symbol with rationale; closes the "had to ask Claude
  separately for the TSLA news picture" gap.

**OMS / audit surfaces:**
- Per-order **audit chain** on `/oms` — state transitions + RiskGate
  decisions + LLM evaluations + **symbol chart inline** (Ichimoku
  cloud fetched from today's snapshot, bare-ticker normalised across
  T212 `_US_EQ` and IG `CS.D.X.MINI.IP` formats).
- Cockpit banner truthful — counts PENDING_APPROVAL as today's
  signals; excludes administrative `reconcile_from_broker` /
  `_monitor` rows from "fills today".
- OMS default-hide noise cancellations (`superseded by newer order`,
  `unapprovable_pending_gates_block`, `broker_not_found_assume_terminal`)
  with toggle pill.
- Signal-coherence guard — frontend refuses to display BUY when
  market_state says WAIT/AVOID. New `apply_swing_strict_demotion` rule
  for medium-long-term holds catches "TSLA squeaks through" cases.
- Per-horizon BUY/WAIT/AVOID pills on Symbol Deep Dive (existing
  HorizonPills wired into the drill-down).

**Daemon schedules** (continuous operation, not once-per-day):
- `com.tradepro.paper-fx` — every 15 min, `--broker ig --strategy
  ichimoku_fx_mr --max-position-value-usd 100000 --lookback-days 14
  --placement-mode auto`.
- `com.tradepro.paper-equity` — every 15 min, US universe + UK
  FTSE100 majors, `--placement-mode auto`. Fills only during
  13:30-20:00 UTC (T212 queues outside hours, smoke test is
  hours-aware).
- `com.tradepro.sentiment` — every 4h, `tradepro-sentiment-score`
  on held positions ∪ algo universe so the news context panel + LLM
  gate are continuously fresh.
- `com.tradepro.paper-watch` — intraday engine (existing schedule).
- All plists checked-in under `docs/launchd/` for cross-Mac sync.

**Memory locks** (rules the next session inherits):
- [project_broker_is_golden_source] — never trust OMS for
  position-aware decisions; query broker `/positions` directly.
- [feedback_no_approval_prompts_ever] — never ask "should I proceed"
  for in-flight TradePro work; just execute.
- [feedback_repo_public_secrets_aws_only] — repo flipped public
  2026-05-28 (GH Actions billing); secrets stay in AWS Secrets
  Manager only.

**Carrying over (next priorities):**
- 🔄 **Strategy P&L attribution dashboard** — per-strategy realised
  + unrealised so the trader can see which strategy is actually
  making money. Existing per-strategy book in `Ledger.StrategyBook`
  has the data; needs a cockpit surface that aggregates across
  brokers + days.
- 🔄 **Drift detection panel** — periodic OMS-vs-broker position
  comparison, surface discrepancies prominently (broker wins always,
  but the operator should SEE the gap before reconciling).
- 🔄 **Basic-auth password rotation** (Task #73, operator's task) —
  `letmein123` was briefly public; rotate via htpasswd on EC2.
- 🔄 **News + earnings calendar productisation** — Finnhub key
  already wired; surface upcoming earnings + recent headlines as a
  cockpit card per held symbol.
- 🔄 **Phase 2 portfolio-aware engine** — buy more / hold / trim per
  holding, horizon-weighted strategy mix. Foundations now in place
  (position-aware seed + engine ledger sync); the rest is the
  decision logic.

---

**Week of 2026-05-25 — Track 2 Core Portfolio complete + Symbol Analysis Card:**

- ✅ **Track 2 — Core Portfolio (Compounder) mode**. All 7 fundamentals
  modules under `core_portfolio/` shipped with Compounder-mode signal
  vocabulary distinct from Track 1's BUY/WAIT/AVOID:
  - ① `quality_scorecard.py` — ROE / ROA / FCF margin / D-E / profit
    margin / current ratio scored 0–10 each, averaged to a 0–5 ★ rating.
  - ② `valuation_layer.py` — trailing/forward P/E, P/B, EV/EBITDA, PEG
    aggregated to ATTRACTIVE / FAIR / STRETCHED / UNKNOWN.
  - ③ `dividend_dashboard.py` — yield, 5y CAGR, payout ratio,
    consecutive-growth years, projected £ income → STRONG / STEADY /
    UNDER_PRESSURE / NONE.
  - ④ `allocation_view.py` — core-sleeve tracker (25% target, ±2.5%
    band), weighted yield, projected income, sleeve vs portfolio %.
  - ⑤ `entry_timing.py` — dip-accumulation alert combining quality +
    valuation + drawdown. Now consumes Lane A's A-F grade as the
    quality signal when supplied (stars fallback otherwise).
  - ⑥ `etf_xray.py` — holdings-overlap detector via min-weight
    intersection (flags VTI+QQQ-style consolidation) + DRIP projector.
  - ⑦ `manual_mf_sleeve.py` — manual-NAV tracker for UK ISA / Indian /
    offshore funds with no live API; FX-normalised to GBP, NAV
    freshness, region + asset-type mix, SIP totals.
- ✅ **Symbol Analysis Card** (`core_portfolio/symbol_analysis_card.py`).
  Platform-level orchestrator that fuses the compare-row technical
  block (bucket / conviction / coherence / exit / RR / sizing / IBKR /
  earnings + news context) with Track 2 fundamentals AND the other
  dev's A-F long-term grade into one card. Returns a single
  `primary_horizon_recommendation` token answering "is this short /
  medium / long-term?" — LONG_TERM_HOLD / MEDIUM_TERM_ADD /
  SHORT_TERM_TRADE / AVOID / WATCH / INSUFFICIENT.
- ✅ **MCP `get_symbol_analysis(symbol, universe, drawdown_pct)`**.
  Single LLM-callable tool wrapping the Symbol Analysis Card.
  Optionally folds the best-Sharpe compare row from the named
  universe for the technical lens.
- ✅ **Lane A — quant_engine** (parallel session, `feat/quant-engine`).
  Trader-provided **complementary systematic-trading framework**:
  Ichimoku-based equity + FX strategies, vol targeting (HOP scalar),
  walk-forward validation, Monte Carlo stress, regime filter (SPY
  200-SMA gate), ensemble combiner, portfolio metrics (Sharpe,
  Sortino, MaxDD, Calmar, CAGR, Omega). Library code is pure (no
  fetching) — production callers must wrap through `cache.py`.
  Signal generators, not portfolio management — fits as an additional
  lens in the Symbol Analysis Card alongside technical / fundamental.

**Week of 2026-05-24 — COMPASS alpha engine + macro regime gate (Sprint 1 + 2):**

- ✅ **Macro Regime Gate** (`macro_regime.py` + `market_context.py`). Three-level
  traffic light (GREEN / AMBER / RED) computed from VIX + HYG credit-spread drawdown
  + 10Y yield trend. `get_risk_mode()` returns 1/2/3; `size_multiplier()` maps to
  1.0× / 0.6× / 0.0×. `MarketContext` now carries `hyg_drawdown_pct` and `risk_mode`
  so every downstream consumer (COMPASS, email digest, paper engine) reads the same
  gate without an extra fetch. Day-keyed `@lru_cache` — one live data pull per session.
- ✅ **COMPASS — Continuous Multi-factor Alpha Scoring** (`compass_scorer.py`).
  6-factor 0–100 score per symbol: momentum (20%), earnings revision (20%), quality
  (15%), sector relative strength (15%), analyst consensus (15%), sentiment (10%),
  valuation (5%). Signal thresholds: BUY ≥72, WATCH ≥55, HOLD ≥40, TRIM <40.
  Macro gate: AMBER dampens BUY→WATCH; RED sets `macro_gated=True`. Conviction grades
  (HIGH/MEDIUM/LOW) and a per-factor evidence breakdown shipped on every result.
- ✅ **Sector Relative Strength** (`sector_rs.py`). 12-week symbol return vs sector
  ETF proxy. Curated SYMBOL_SECTOR_ETF map covers 40+ names (NVDA/MU/ASML→SOXX,
  AAPL/MSFT→XLK, HSBA.L/AZN.L→EWU, VUKE.L→SPY). Unknown symbols fall back via
  yfinance sector lookup, then SPY. Score mapping: +15%→10, down to <-15%→1. Feeds
  directly into COMPASS sector_rs factor.
- ✅ **EPS Revision Tracker** (`eps_tracker.py` + `cli/refresh.py --eps-snapshot`).
  Weekly snapshots of yfinance `forwardEps` per symbol stored at
  `~/.tradepro/eps_snapshots/`. `get_eps_revision()` returns 90-day delta + direction
  (up/down/flat/insufficient_data). `batch_record_snapshots()` concurrent via
  `ThreadPoolExecutor`. Weekly cron entry point:
  `tradepro-refresh --watchlist <X> --eps-snapshot`. Feeds COMPASS earnings_revision
  factor; ETFs + no-coverage symbols skipped gracefully.
- ✅ **Signal Ledger** (`signal_ledger.py`). Append-only JSONL evidence log at
  `~/.tradepro/signal_ledger.jsonl`. Every COMPASS / CATALYST signal fired is
  immediately persisted with UUID, entry/stop/target, expires_at. `close_signal()`
  stamps outcome (HIT_TARGET / STOPPED_OUT / EXPIRED / MANUAL_CLOSE), exit_price,
  return_pct, holding_days. `compute_stats()` returns hit_rate_pct + expectancy_pct
  per model/symbol/lookback window — the evidence base that answers "is the model
  actually working?" Atomic rewrite via `.tmp` file for safe in-place updates.
- ✅ **COMPASS wired into compare.py**. Computed once per symbol in the hot path
  (after the ichimoku_promote block, before `for r in sym_rows:`). Result stamped on
  every row: `compass_score`, `compass_signal`, `compass_conviction`,
  `compass_breakdown`. Full try/except guard — a scorer failure never breaks a
  compare run. Sector RS + EPS revision fetched best-effort alongside.
- ✅ **CompassMomentum intraday paper strategy**
  (`paper/strategies/compass_momentum.py`). Entry: COMPASS ≥68 AND RSI(14) <62 AND
  price >SMA20. COMPASS resolved once per symbol per session (lazy on first bar, then
  memoised). Stop = entry × (1 − stop_pct, default 2%); target = entry + 2× risk.
  Exit: RSI exhaustion ≥72 OR hard stop OR EOD flatten. Risk-envelope size cap
  applied. `confidence` field on every Order derived from COMPASS score/100 so the
  pre-trade gate sees a semantically meaningful probability.
- ✅ **Paper gate recalibration** (`cli/intraday_engine.py`). `autoPlaceConfidenceThreshold`
  0.85 → 0.72; `minRiskRewardRatio` 2.0 → 1.5. Previous defaults produced zero fills
  — new values are calibrated to the realistic signal distribution we now have with
  COMPASS + CompassMomentum.

**Week of 2026-05-18 — unicorn-grade foundations:**

- ✅ **Phase 5 — Postgres migration of every store**. All 9 in-memory +
  JSON-file stores (pending orders, paper snapshots, paper backtests,
  paper strategies, watchlists, settings, compare cache, documents,
  heartbeats) now backed by Postgres on the same EC2 host. Survives
  redeploys. Schema also seeds the future event-sourcing tables.
- ✅ **Phase 6 — Event-sourced orders + fills + domain log**. Every
  paper-trading order writes to the append-only `orders` table with a
  `decision_trace`; risk decisions (approve / reject) leave an
  audit trail; `events` table seeds the Phase 7 SSE stream. Read API
  at `/api/orders/`, `/api/orders/{id}`, `/api/events/`.
- ✅ **AWS Secrets Manager bundle**. Single `tradepro/all` JSON in
  eu-north-1, read by both the Mac engine and the .NET API at boot.
  Cross-region IAM provisioned via the ccit-infra TF module.
- ✅ **Daily email digest plist** (`com.tradepro.email-digest`,
  23:00 UTC) + dry-run + real-send verified end-to-end.
- ✅ **Daily paper-trading plist** (`com.tradepro.paper`, 14:30 UTC).
  Reads symbol list from env, omits `--placement-mode` so the engine
  resolves it from `/api/settings` per run.
- ✅ **UI toggle for paper-trading placement mode**. Settings page
  has Auto / Manual pills wired to `/api/settings`; the Mac engine
  fetches the value at session start. Survives every redeploy via the
  Postgres settings store.
- ✅ **HOLD-IN / HOLD-OUT split** on the Backtest page so
  `1 BUY + 0 SELL + 3 HOLD-IN + 3 HOLD-OUT` reconciles in-place with
  the "4 of 7 currently long" line — math now adds up at a glance.
- ✅ **STRATEGIES.md** — canonical reference for all 3 strategy layers
  (.NET signal, Python paper, horizon scorers) including the
  instrument-strategy fit problem (MTUM/RSI-MR case).
- ✅ **EVALUATION.md** — the five lenses for telling whether a
  strategy is working on a given symbol, with the AVGO worked example.
- ✅ **VISION.md** — unicorn-architecture target: 8 properties + 6 PR
  principles + 8-phase arc.
- ✅ **aws-redeploy.yml SSM output truncation fix** — pull/up output
  routed to /tmp logs, only the tail shipped back. Stops the workflow
  reporting "failed" on every successful deploy.
- ✅ **Strategy leaderboard on Decide page + MCP tool**. Per-symbol
  ranking of all 7 strategies by Sharpe, with action labels collapsed
  to BUY / SELL / HOLD-IN / HOLD-OUT and a delta vs the buy-and-hold
  null model. Frontend widget on the expanded panel; MCP exposes the
  same shape via `get_strategy_leaderboard(universe, symbol)` so an
  LLM agent sees the same ranking a human does. Closes the user's
  "how do we see which strategy is handling that symbol better"
  question.
- ✅ **Phase 6.5 — instrument-strategy fit**. Closes the MTUM
  contradiction. Every symbol classified via `factor_types.py`
  (momentum / value / quality / low_vol / broad_equity / bond /
  commodity / crypto / single_stock / ...); the consensus engine
  excludes structurally-incompatible strategy votes (RSI mean-
  reversion on MTUM, breakouts on USMV, etc.). UI banner explains
  exclusions; leaderboard greys out excluded rows. New MCP tool
  `get_instrument_fit(symbol)` returns the classification + the
  list of compatible / incompatible strategies with a one-line
  reason per factor type. STRATEGIES.md updated.
- ✅ **Phase 7 — SSE real-time event stream**. Postgres
  LISTEN/NOTIFY trigger fires on every events INSERT; .NET
  `EventStream` service holds a long-lived listener and pushes
  rows through `/api/events/stream`. Frontend `useEventStream`
  hook subscribes via streaming fetch (EventSource can't carry
  bearer tokens), reconnects with `since=<lastSeq>` on drop. Paper
  page's Pending Orders panel auto-refreshes the moment any
  order-shaped event lands — Approve/Reject no longer requires a
  tab click to see the change reflected. A small "live" pip in the
  tab bar shows stream-connected state.
- ✅ **`get_hypothetical_return` MCP tool**. Answers "if I'd bought
  X on date Y, what would my return be today?" using split +
  dividend adjusted closes — the question that kept surfacing
  while validating signals against history. Returns total +
  annualised return, peak/trough, max drawdown along the way, and
  optional dollar return when quantity is given. Smoke-tested:
  AAPL 2020-01-02 → 2024-01-02 = +163% / 27.5% annualised /
  -31% mid-hold drawdown.
- 🟡 **Data-source decision — stay on Yahoo for daily, defer
  intraday upgrade.** Reviewed TradingView vs Alpha Vantage vs
  Polygon.io. Verdict: daily-bar strategies (what we ship) don't
  need an upgrade — `get_hypothetical_return` already answers
  "what would I have made" correctly from Yahoo's adjusted
  closes. Intraday (1m / 5m) only matters when Phase 3's
  swing-trade scorers want hourly bars. Upgrade path when
  needed: **Polygon.io starter at $30/mo** (deep intraday, tick
  data, options chains — what TradingView and Robinhood use
  under the hood). NOT TradingView (charts only, not an API),
  NOT Alpha Vantage (shallow history, brittle rate limits).
- 🟡 **Comprehensive MCP coverage push.** Every backend endpoint
  Claude might need to "interrogate progress" gets an MCP
  wrapper. Existing: compare, market_state, news, regime_history,
  strategy_leaderboard, instrument_fit, portfolio, portfolio_status,
  portfolio_signals, horizon_signals, hypothetical_return,
  t212_instruments, health, fundamentals, returns, evaluate_symbols,
  run_comparison, universes. Adding: paper trading (pending_orders,
  approve_paper_order, reject_paper_order, get_order, list_orders,
  paper_snapshot, paper_backtest_reports, list_paper_strategies),
  track-record (hitrate, signal_scan, evaluate_signal), events
  (earnings_calendar, analyst_recommendations, analyst_upgrades),
  market data (get_candles), control plane (get_settings,
  set_paper_mode, list_watchlists, get_watchlist).
- 🟡 **Phase 6.7 — `/api/*` lockdown with bearer-or-Firebase auth.**
  AllowedUsers policy extended to accept EITHER a verified Firebase
  ID token (browser path) OR the static ingest-token bearer (MCP +
  Mac worker path). Single secret — same `tradepro/all/ingest-token`
  from AWS Secrets Manager unlocks both writes (existing) and reads
  (new). MCP `_get/_post/_put` helpers attach `Authorization: Bearer
  <token>` from `~/.tradepro/credentials` automatically. Lockdown
  activates only when `Firebase__RequireAuth=true` is set on the
  EC2 — code is shipped, env flag is the deploy switch. Closes the
  "anyone with the IP can read live T212 positions" exposure (we
  verified with curl: T212 portfolio + pending orders + settings
  were all anonymously readable).
- 🟡 **Phase 6.8 — Data validation + provenance layer.** Multi-
  source consensus storage in S3+Athena (Parquet, partitioned by
  symbol/year), one row per `(symbol, date, source)`. Reconciler
  produces a `bars_consensus` table with `quality_flags` array:
  `missing_source`, `minor_price_drift` (>0.5%), `split_adjustment_
  disagreement` (>5%), `ohlc_invalid_<source>`, `split_disagreement`,
  `zero_volume_in_some_source`. Backtest results inherit the worst
  flag they touched and expose `data_quality.confidence: high/medium/
  low` so the UI can render a yellow or red banner. This is the
  data-layer twin of the explicit sentiment-demotion rule — the
  product principle "make data-quality issues visible to users"
  made operational. Sequencing:
    1. S3 + Parquet schema, Yahoo + Stooq ingestion, DynamoDB
       checkpoint table for idempotent backfills (£0, ~weekend)
    2. Cross-validation logic with the 0.5% / 5% thresholds (£0)
    3. MCP tools `get_bars(symbol, from, to, resolution, source,
       require_quality)` + `get_data_quality_report(symbol, from,
       to)` (£0, ~hour)
    4. **Wire backtest results to inherit quality flags + render
       confidence badges — the visible win, do BEFORE paying for
       EODHD** (£0, ~half-day)
    5. Subscribe to EODHD EOD All World as primary (€19.99/mo
       personal-use; jumps to $399/mo Internal Use the moment a
       second user appears — strategic gotcha to know upfront)
    6. IBKR TWS spot-checks on a sampled basis (£0, ~half-day)
    7. Tiingo Power as US-only validator IF step 4 surfaces
       US-name disagreements ($10/mo, ~hour)
  Skip survivorship-bias / historical S&P constituents until at
  least step 4 ships — useful eventually, not the bottleneck today.
- 🟡 **Phase 6.9 — Tick storage for intraday backtesting.** Once
  6.8 is shipped, extend the schema to hold 1-minute bars +
  raw tick data for the intraday strategies (`paper/strategies/`:
  ORB, VWAP mean-reversion, intraday Bollinger, MA crossover).
  Storage cost: ~5GB/symbol/year of tick data uncompressed, ~1GB
  Parquet-compressed. S3 standard at $0.023/GB/mo = ~$0.02/symbol
  /mo. Source: Polygon.io ($30/mo stocks starter) for tick + 1m
  history; falls back to EODHD intraday on the EU side. Unblocks
  proper paper-strategy backtests instead of the synthetic-bar
  approximation we use today. Folder refactor: promote
  `paper/strategies/` → top-level `intraday/`, keep `paper/`
  for paper-trading INFRA only (engine, ledger, brokers).
- ✅ **Phase 6.5.5 — Trend-gate-failure bug (BABA case study).** Fixed
  in commit `832d82a`. Bounce-zone BUY block in `_classify()` now has
  an `if above is False: return ("WAIT", ...)` guard. Belt-and-braces
  via `BUG-001` veto in `compute_conviction()` + `cap_bucket_at_low_conviction()`.
  Behave scenario `baba_bounce_zone.feature` covers the exact geometry.
- ✅ **Phase 6.11 — Analyst-upgrades feed audit.** Root cause: Finnhub
  free tier returns HTTP 200 with `[]` for `/stock/upgrade-downgrade`
  (paid plan required — not a code bug). Fixed by: exposing `planGated`
  flag in the .NET API response, propagating through Python `analyst_actions.py`
  and MCP `tools.py` so the UI shows "upgrade data not available on free
  Finnhub plan" rather than a misleading "0 upgrades". The monthly
  `analyst_recommendations` endpoint (free tier) is unaffected.
  Commit: `9719e35`.
- 🟡 **Phase 6.12 — Fundamentals + valuation layer.** Currently
  TradePro is a "sophisticated technicals + sentiment engine" —
  it should either be positioned that way or close the gap to
  Stock Rover / Morningstar / Zacks by adding revenue growth,
  margins, FCF, DCF / PT framework. Without this, the BUY signal
  fires on the next Lucid or Peloton because price is 28% off
  the 52w high with RSI 50 — same technical setup, totally
  different investment thesis. Data sources: existing
  `get_fundamentals` MCP tool returns expense ratio / AUM /
  top-10 holdings (ETF-shaped); needs to also surface revenue
  / margins / FCF / EPS history for single stocks. Provider:
  Finnhub free tier has basic financials; SimplyWallSt /
  StockAnalysis.com API have richer data ($30-80/mo). Surfaces
  as a new Section 11 on the Symbol Deep Dive page after the
  current 10. Catalyst-flag layer (earnings, analyst day,
  major product launches) — same domain — extends this phase.
- 📝 **Data-feed coverage map — concrete priorities.** What
  improves correct-decision quality, ranked by leverage:
    1. ✅ **Forward earnings calendar on compare row** (task #66) —
       "EPS in Xd" badge added to compare matrix Verdict cell.
       Red ≤14d (earnings danger zone), amber 15-30d, grey beyond.
       `earnings_signal.upcoming` typed in CompareRow; badge is
       absent for ETFs / when Finnhub disabled. `feature/upcoming-earnings-badge`.
    2. **Per-strategy regime data on compare row** (task #66) —
       unblocks Section 8 of the Deep Dive. Already computed
       on the Mac, just not folded into the row. ~0.5d.
    3. **Symbol → tags map** (task #66) — unblocks Section 9
       peer-comparison. Hardcoded list per universe today;
       needs a tags table. ~0.5d.
    4. **EODHD EOD All World + Yahoo + Stooq consensus** (Phase
       6.8) — per-bar quality flags. €19.99/mo. The data-layer
       moat: nobody else shows backtest confidence badges.
    5. **Polygon.io stocks starter** (Phase 6.9) — intraday tick
       + 1m history. $30/mo. Unblocks validated intraday
       backtests, which is the prerequisite for trusting any
       auto-place automation (task #69).
    6. **Fundamentals + valuation feed** (Phase 6.12) — to fix
       the "BUY on Lucid because price is dipped" failure mode.
       Finnhub free or SimplyWallSt $30-80/mo. The gap vs
       Stock Rover / Morningstar.
    7. **Catalyst-flag feed** — major product launches, FDA
       approvals, M&A events. The gap vs institutional tools.
       Provider TBD (Estimize / Benzinga both have APIs).
  Reviewer's verdict 2026-05-20: "Transparency + backtested
  hit-rates + regime-aware backtests + ensemble voting puts
  TradePro ahead of most retail tools. Gap to Stock Rover =
  fundamentals layer. Gap to institutional = event awareness."
- 📝 **BABA worked-example case study (2026-05-20).** External
  reviewer ran TradePro's full BABA pipeline and benchmarked
  against TipRanks / CNN / Wall Street consensus. Three
  high-signal findings folded into 6.5.5 (trend-coherence bug),
  6.11 (analyst-upgrades dropout), and 6.12 (fundamentals gap).
  Worth keeping the trace as a regression-test fixture: any
  future change to compute_bucket or the rationale layer should
  reproduce this case correctly.
- 📝 **MU bucket = AVOID + swing = 5/8 BUY — feature, not bug.**
  `compute_bucket` in `compare.py:365` is intentionally hierarchical:
  `price_verdict` (set by `market_state` from RSI / SMA200 / 52w
  range) drives the bucket; horizon signals only DEMOTE BUY→WAIT,
  never PROMOTE AVOID→anything. The swing horizon is a SEPARATE
  scorer optimised for short-term entry-timing. So "bucket=AVOID
  + swing=5/8 BUY" is the system correctly surfacing horizon
  disagreement — the long-term consensus says "broken setup", the
  short-term scorer says "swing bounce". The actual gap is UI: the
  Decide page doesn't surface this disagreement in plain English.
  Fix is presentation, not logic: render "Long-term: AVOID. Swing:
  BUY 5/8. These conflict because <reason>" on rows where the two
  buckets diverge. Tracked separately — the reviewer correctly
  identified this as differentiation, not a defect.

**Week of 2026-05-10 — chart depth + rationale precision:**

- ✅ **Inline `PriceHistoryChart`** on Research + Decide pages — 5y
  split-adjusted line + SMA(200) overlay + 52w high/low reference.
  Range presets (1M / 3M / 6M / YTD / 1Y / 5Y / All), recharts `Brush`
  for free-form zoom, "today" marker, dip/near-highs range zones,
  volume strip below sharing the brush, dated dots at the 52w extremes
  so the user can read live-vs-stale floor at a glance.
- ✅ **Rationale prompt v3 → v4** — guard against the "N/A as
  single-stock analysis" hallucination for ETFs (v3); ban round-number
  RSI/SMA filler, ban LLM-computed percentages, ban SMA(50), restrict
  year mentions (v4). Cache audit: 35% of LLM rationales were getting
  rejected; v4 targets the recurring offenders.
- ✅ **Verifier enrichment** — verification notes now carry the
  offending sentence (`unsupported number: 999% — in sentence: "..."`)
  so the user can see *which claim* to discount, not just the bare
  number. Frontend pill goes amber ⚠ when notes > 0 instead of the
  misleading green ✓.
- ✅ **`closes_30d` field on `MarketState`** — wires up the
  already-committed `email_charts.buy_sparklines_png()` so BUY rows in
  the daily digest get a 30-bar mini-chart per name.

**External reviewer suggestions (2026-05-11) — triage:**

- ✅ **Already shipped** — reviewer's #5 (risk rating, commit `61bb7e5`) and #10
  (Gem Hunter, commits `57a9c3b` + `ac7a55b`). Visible as `RiskPill` and
  `GemsCard` on the Decide page. Skip re-prioritising.
- 🟢 **Validates existing direction** — #1 data persistence (Phase D, below),
  #3 historical P/E store (Phase B / D2 snapshot store), #7 Monte Carlo
  (Phase B), #8 Lambda+EventBridge worker (Phase D3), #9 live price feed
  (Phase 7). Already on the roadmap; reviewer's priority weighting noted.
- 🆕 **New, accepted** — added below:
  - ~~**Symbol autocomplete**~~ — already shipped: SymbolPicker.tsx has
    full debounced typeahead against `/api/instruments/search` (Yahoo
    search), keyboard navigation, T212 badge for tradeable symbols.
    Reviewer wasn't aware.
  - **Insider trades + analyst recommendations layer** — `yfinance.Ticker
    .insider_purchases` + `finnhub.stock.recommendations`. New signal family
    on the swing scorer (currently 5 layers, would become 6). Lives in
    `tradepro_strategies/insiders.py`. Decision trace gains an "insider"
    row. ~1-2 days. Notes: be careful about false positives — automatic
    10b5-1 plan sales/buys look like discretionary insider trades but
    aren't; filter on `acquisition_or_disposition == 'D'` correctly.
  - **S3-archive-in-push** — the `tradepro-archive` terraform module
    (S3 bucket + writer creds) is provisioned but `push_to_api.py` doesn't
    upload there yet. Add an opt-in `archive_to_s3()` call alongside the
    API push so we have replay history before Postgres lands. ~2 hrs.
- ❓ **Deferred — needs decision** — #2 earnings markers + corporate-actions
  markers on chart (already in flight queue below; the reviewer's pairing
  it with #4 insider trades raises the question of whether to land them as
  one "events-on-chart" PR — see backend-endpoint follow-up below).

**Stress-test reviewer suggestions (2026-05-11) — triage:**

Worth noting up front: TradePro is a **research / signal tool**, not a
live execution platform. The reviewer's risk-management suggestions are
about **backtest realism** (would change the historical CAGR / Sharpe
numbers) rather than runtime trade safety — users decide whether to
buy on their own. That framing changes the priority of each item.

- ✅ **Already shipped** — reviewer's "What's Working" list matches
  current state: range-position guard (VUKE class), 5y peak context
  (INRG class), two-tier sentiment demotion, decision trace, fee +
  stamp-duty model, NaN/inf safety, v4 rationale prompt.
- 🟢 **Validates existing direction** —
  - **Sideways-market volatility filter** — partially handled today by
    the swing composite's earnings-event layer + sentiment demotion;
    a hard `annualised_vol < 15% → HOLD` rule could fit in the
    `_classify` ladder but risks suppressing valid mean-reversion
    setups on low-vol ETFs (VUKE / VGOV are <12% vol and BUY-able).
    Skip unless we see whipsaw losses in the cache.
  - **LLM hallucination rate** — already targeted by v4 prompt rollout;
    re-audit after worker has pushed under v4 for a week.
- 🆕 **New, accepted into roadmap** — added below:
  - **Backtest stop-loss option** (HIGH) — trailing stop + max-loss-per-trade
    as `BacktestConfig.StopLossConfig` flags, default off so existing
    suite stays comparable. Lets the user A/B "buy_and_hold vs same
    strategy with 8% trailing stop" in the Backtest UI. ~1 day.
  - **Crash-protection branch in `_classify`** (MEDIUM) — new rule:
    `10-day return < -8% AND below SMA200 → AVOID with "active crash"
    reason`. Catches the "falling knife" gap reviewer flagged. Sits
    *before* the bounce-zone BUY rule so a confirmed crash always
    wins over a mean-reversion bid. New scenario in
    `features/market_state_classify.feature`. ~3 hrs.
- ❓ **Deferred — needs design**:
  - **Position sizing (Kelly / fixed-fractional / vol-targeted)** — touches
    the backtest engine, the swing composite, and the (future) portfolio
    simulator. Better to land alongside Phase B (portfolio simulation)
    than as a standalone change. Logged here so it isn't lost.
  - **Correlation filter** — same: belongs in Phase 2 (portfolio-aware
    engine), which already plans buy-more / hold / trim across positions.
    Logged in `project_phase2_portfolio_aware` memory.

**In-flight / next up (do not lose):**

- ✅ **AWS deploy — LIVE** as of 2026-05-11. Tradepro is on its own
  t4g.small at `http://16.60.201.137/` (instance
  `i-01b390204472e4b9f`, EIP preserved across stop/start). Frontend
  on port 80 with nginx proxying `/api/*` to the .NET API container,
  plus port 8081 still exposed for direct API debugging. Auto-stop
  at 22:00 UTC, auto-start 08:00 UTC weekdays.
  - Terraform module: `~/sourcecode/ccit-infra/modules/tradepro-demo/`
  - Deploy workflows: `.github/workflows/aws-{bootstrap,build-push,
    redeploy,set-env,start,stop,status}.yml`
  - Operator guide: `docs/aws-deploy.md` · architecture:
    `docs/aws-architecture.md`
  - Follow-up: rotate the PAT in SSM (`/ccit-dev/tradepro/github-deploy-pat`)
    — current one is invalid. Bootstrap workflow ships the compose
    file via runner checkout so it's not strictly needed, but
    `aws-redeploy.yml` still git-fetches on the box and will fail
    until the PAT is fresh. Either rotate the PAT or refactor
    redeploy to use the same checkout-and-ship pattern.
- ✅ **Events-on-chart bundle** — all three layers shipped:
  - ✅ **Earnings markers** (PR #21) — `GET /api/marketdata/earnings`;
    green/red/grey "E" dots. `EarningsMarker` promoted to `types.ts`.
  - ✅ **Corporate actions** (PR #22) — `GET /api/marketdata/corporate-actions`;
    amber "D" = dividend ex-date, teal "S" = stock split.
  - ✅ **Insider buys** (PR #23) — `GET /api/marketdata/insiders`; green "I"
    chips for discretionary purchase transactions only (sales excluded —
    10b5-1 auto-sell noise makes them too ambiguous for a directional signal).
  All three parsed from Yahoo Finance's v8/finance/chart overlay + quoteSummary
  API via existing `YahooFinanceProvider` HttpClient (no new DI). Wired into
  `Signals.tsx` with parallel symbol-change fetches; silent failure on each
  so the chart degrades cleanly if any one feed is unavailable.
- ✅ **Symbol autocomplete** — shipped (SymbolPicker.tsx →
  /api/instruments/search → Yahoo). Includes debounce, keyboard nav,
  T212 "tradeable" badge. Cures the "NV → 500" footgun.
- ⏳ **S3 archive in push pipeline** — `tradepro-archive` bucket exists
  (terraform `modules/tradepro-archive` + writer creds in outputs); the
  push CLI doesn't upload there yet. Add `archive_to_s3()` after a
  successful `/api/ingest/compare` so we have replay history before
  Phase D2 lands. Opt-in via `TRADEPRO_S3_ARCHIVE=1` env. ~2 hrs.
- ✅ **Backtest stop-loss option** — shipped. `BacktestConfig.stop_loss`
  block (trailing pct + max-loss pct variants) in .NET `Simulator.cs`;
  UI toggle on Simulations.tsx. Default OFF preserves reproducibility.
- ✅ **Crash-protection rule in `_classify`** — shipped. `ACTIVE_CRASH_10D_PCT = -8.0`
  in `market_state.py`; fires AVOID before bounce-zone BUY. Behave scenario
  covers the exact geometry.
- ⏳ **Re-audit rationale cache after v4 prompt rolls out** — target
  rejection rate <10% (currently 35%). If v4 doesn't move the needle,
  the next move is a *model* audit: which model is producing the
  round-number filler? Bigger / instruction-tuned models hallucinate
  these less.
- ⏳ **UI smoke test of `PriceHistoryChart` enhancements** — the four
  chart commits (`1dc3186`, `d5e94e7`, `de92e7e`, `135e687`) shipped
  with `tsc --noEmit` clean but no browser run. Smoke before next
  deploy: zoom presets work, brush handles drag, today marker visible,
  range zones tinted correctly, volume strip syncs to the brush.
- ⏳ **`build/`, `tsconfig.tsbuildinfo`, `.idea/` removal from git
  history** — added to `.gitignore` in `2b74a26` but if they were
  previously committed somewhere, a follow-up `git rm --cached` may
  be needed (probably weren't — was the first time we noticed them).

**Week of 2026-05-06 → 2026-05-09 — horizon engine + portfolio surface:**

- ✅ **Range-position guard on BUY** (`market_state.py`) — VUKE-class
  fix. Symbols at ≥70th pctile of 52w range get downgraded BUY → HOLD;
  ≥80th pctile is hard-capped at WATCH. Covers the "5% off the 52w
  high after a +24% YoY run is not a dip" case the live VUKE.L test
  surfaced.
- ✅ **Horizon Classification Engine** (`horizons.py`,
  TRADEPRO-SPEC-001 §6) — three independent verdicts per symbol:
  swing (1–8w), long-term (6–18m), passive (3–5y). Each has its own
  0–8 score, signal grade and reasons. Wired into compare payload as
  `horizon_classification` + new MCP tool `get_horizon_signals(symbol)`.
- ✅ **Trading 212 portfolio surface** — live-mode auth fix (single-key
  vs Basic), 30s positions cache to dodge T212's 1-req/1s rate limit,
  `HoldingsHealthCard` on the Decide dashboard, full `Portfolio` page,
  email digest "What you hold" section. New `get_portfolio_signals`
  MCP tool returning per-position BUY_MORE / HOLD / TRIM with narrative.
- ✅ **Email digest charts + PDF attachment** — bucket donut, holdings
  P&L bar, BUY-candidate sparklines inline in HTML body. Multi-page
  PDF with cover, methodology, holdings, per-symbol detail (decision
  trace + horizon scores + sentiment + analyst targets), glossary.
- ✅ **Two-tier sentiment demotion** (`compare.py`) — mean ≤ −0.45 with
  ≥3 material-negatives demotes any bucket → AVOID, distinct from the
  existing −0.30 → WAIT. Differentiates "news backdrop is bad" (WAIT)
  from "news flow is genuinely hostile" (AVOID).
- ✅ **P/E hybrid valuation lens** (`cross_sectional.py`) —
  `bucket_by_valuation` orchestrator picks P/E quartiles for stock
  baskets (NVDA no longer mis-flagged "expensive" purely for not
  paying a dividend), falls back to dividend-yield quartiles for ETF
  baskets where P/E isn't reported. Honest gap: still BASKET-relative,
  not vs symbol's own historical median (snapshot store parked).
- ✅ **AVOID demotion on extreme negative sentiment** (above) +
  **AMZN-class fix** for sentiment confused-with-WAIT.
- ✅ **Worker pushes finally landing** (`push_to_api.py`) — credentials
  loader fell back to env vars when `~/.tradepro/credentials` is
  absent (docker worker had `TRADEPRO_API_URL` + `TRADEPRO_API_TOKEN`
  set in compose env but the loader exited early). Heartbeats now
  reach the api over the compose network.
- ✅ **Finnhub forward-earnings calendar wired** — `FinnhubEarningsEvent`
  parsing fix (Quarter/Year are JSON ints not strings); EPS-warning
  copy in the digest now fires when a holding has earnings within 14d.
- ✅ **MCP tool reliability** — `_get` default timeout 10s → 30s,
  `get_portfolio_signals` parallel-fetches universes; tunable via
  `TRADEPRO_MCP_TIMEOUT` env. Stops Claude Desktop's visualiser from
  giving up mid-tool-call.
- ✅ **UX polish** — Decide is now the index route (Scanner moved to
  `/scanner`); Mac → Strategy Engine rename in user-visible strings;
  Backtest page gets the existing `SymbolPicker` autocomplete + a
  popular-tickers chip row; Help page widened from 820 → 960px.
- ✅ **Docker build hang fix** — `BUILDX_NO_DEFAULT_ATTESTATIONS=1`
  documented in compose comments after the buildx provenance step
  hung during a rebuild.

**Open follow-ups from this week:**

- [x] Spec P2: LLM rationale prompt with horizon context (3 horizon-
  specific sentences per symbol per TRADEPRO-SPEC-001 §7) ✅ 2026-05-09
- [x] Help-page strategy visualisations (SMA crossover, RSI bands,
  MACD histogram, Donchian channel, 52w range, return distribution —
  visual learners). 6 inline recharts demos in Indicators + Risk
  metrics topics. ✅ 2026-05-09
- [x] Help-page **Data Sources** topic listing every external feed
  with status, cost, what it provides ✅ 2026-05-09
- [x] Health page **external-source status** card (Yahoo / Finnhub /
  Ollama / T212 with last-success age and degraded indicator)
  ✅ 2026-05-09 — `/health/integrations` endpoint + Data sources
  panel on the Health page
- [ ] **Historical P/E snapshot store** to replace basket-relative as
  the long-term valuation lens (spec §10 Q1)
- [ ] **SEC EDGAR** integration — free 10-K/10-Q filings, would feed
  the snapshot store and the rationale layer
- [ ] **Insider trades + recommendation trends** — yfinance +
  Finnhub both expose these and we don't currently use them
- [ ] **Portfolio simulation engine** — Monte Carlo + stress + DCA on
  live portfolio. New Phase B added below. To be discussed.
- [x] Research + Backtest "Run all 5 strategies" UX — replaces
  per-strategy selection friction with a single fan-out + consensus
  view. Symbol picker autocomplete on both pages. ✅ 2026-05-09

---

## Where we are now (April 2026)

Concrete, working today on `main`:

**Research stack (Python, runs on Mac M-series):**
- Five rule-based strategies: buy-and-hold, SMA crossover, RSI mean
  reversion, MACD signal cross, Donchian breakout.
- Comparator (`tradepro-compare`) that runs `N strategies × M symbols`,
  outputs a JSON payload with backtest stats (CAGR, Sharpe, max-DD),
  per-regime stress evidence (13 historical windows: dot-com, GFC,
  COVID, 2022 rate shock, etc.), per-symbol market-state verdict
  (BUY / HOLD / WAIT / AVOID) with a transparent decision trace, a
  per-symbol Wall Street consensus snapshot from Yahoo, and macro
  context (VIX, 10Y, S&P drawdown, active stress regimes).
- Five ETF watchlists pre-defined: `etf_uk_core`, `etf_us_core`,
  `etf_us_sector`, `etf_factor`, `etf_all` (35-symbol union).
- Local Parquet cache, idempotent refresh, manifest + JSONL event log
  per run.

**API (.NET 8):**
- Read endpoints (Firebase-auth): `/api/marketdata/*`,
  `/api/signals/*`, `/api/simulations/*`, `/api/watchlists/*`,
  `/api/compare/{universes, latest}`.
- Ingest endpoint (static-token auth): `/api/ingest/{compare,
  backtest, scan, model_prediction}`.
- Pluggable provider abstraction (Yahoo / Stooq / Binance) with
  Yahoo as the only one currently advertised.

**Frontend (React + Vite):**
- Dashboard, Scanner, Signal detail (with hit-rate card), Simulations,
  Charts, Help, **Compare ETFs** (the headline page).
- Compare page: bucket triage (BUY today / WAIT / AVOID), strategy
  matrix (rows = ETFs, columns = strategies, cells = LONG/flat),
  expand panel with full decision trace, all-strategies stats, regime
  evidence, and Wall Street cross-check.

**Dev infra:**
- `docker compose up` brings up the API and frontend with hot reload.
- `tradepro-push` ships JSON from the Mac to the API over a static
  bearer token.

**Deploy:**
- Frontend → Firebase Hosting on push to `main`.
- API → Azure App Service on push to `main`.
- Strategies stay on the Mac.

---

## Key assumptions

These are the constraints every design decision sits on top of. Argue
with one, the plan changes.

| # | Assumption | Implication if it changes |
|---|---|---|
| A1 | **Single user.** No multi-tenancy. Auth is "is it me, or someone I let in?" — a static ingest token + a Firebase UID whitelist. | Multi-tenant SaaS would need user IDs everywhere, per-user data partitioning, billing, and rate limiting. |
| A2 | **UK-resident retail investor; ETF-led, individual stocks layered on later.** Defaults: GBP, LSE `.L` symbols, UK 0.5% stamp duty on buys. ETFs are the lead use-case ("which ETF should I hold for years?"). Individual stocks (FTSE 100, US mega-caps) are in the watchlists today but get a richer treatment in Phase 5b — fundamental ratios + earnings narrative — once ETF execution is solid. | A US-resident default would change fee model, watchlist, and tax-wrapper modelling. Going stocks-first instead of ETF-first changes which signals matter most (P/E + earnings vs Sharpe + drawdown). |
| A3 | **Daily bars today, real-time feed planned.** Currently EOD only — strategies fire on close-to-close events. Phase 7 introduces an optional intraday/real-time feed (Alpaca, IBKR, Polygon) for the symbols a user actively holds, gated per-watchlist so we don't burn quota on the whole universe. | When real-time lands: data layer adds streaming source(s), a new "live" tab on Compare, and bucket assignments refresh on tick rather than once per day. |
| A4 | **The Mac is the source of truth for compute.** Heavy work (backtests, model training) runs locally on the M-series. The API only stores + serves the JSON the Mac pushes. | Moving compute to the cloud means provisioned infra costs, GPU/CPU plans, and probably AWS Lambda or a managed batch service. |
| A5 | **Yahoo Finance is primary, best-effort.** Free, no key, but rate-limited and subject to upstream changes. Failures are tolerated (return empty rather than crash). | If Yahoo gets blocked or sunsets the unofficial API, we'd need Alpha Vantage / Finnhub / IBKR with API keys + paid tiers. |
| A6 | **Rule-based strategies, not ML — yet.** Every verdict is explainable: a human can read the rules in `market_state.py:_classify`. | Adding ML means model-versioning, training pipelines, drift monitoring, and a different transparency story (feature importance instead of an `if`-ladder). |
| A7 | **Recommendations are decision aids, not advice.** No regulated advice claim. The UI says so explicitly. | Regulated advice means FCA / SEC compliance, custody questions, and is out of scope. |
| A8 | **Backtests use `adj_close` (total return).** Dividends and splits are baked in. Cross-currency rankings (e.g. `etf_all`) are valid for Sharpe / CAGR % / max-DD % because those are currency-neutral; absolute fee accounting is per broker. | Mixing fees naively across currencies would distort return ladders. |
| A9 | **Free-tier infra by default.** Firebase Hosting Spark + Firestore Spark + Azure App Service F1 covers single-user load at £0/mo. F1 sleeps after ~20m idle. | Going public or sharing the URL needs a B1+ App Service or a CDN/Cloudflare front. |
| A10 | **AWS migration is planned but undated.** Today's deploy targets Azure App Service. Code is portable (env-driven URLs, no Azure SDKs). | When the migration happens, only `azure-api-deploy.yml` + `appsettings.json` env keys need to change. |
| A11 | **ETFs aren't analyst-rated.** Yahoo returns null `recommendationKey` for baskets. The Wall Street cross-check is informational for stocks; for ETFs it shows "not rated" and doesn't degrade the BUY decision. | If a future provider rates ETFs, we'd surface that rating in the cross-check. |
| A12 | **Manual `tradepro-push` for now.** No auto-refresh; data freshness is whatever the operator last ran. | Phase 4 introduces scheduled `launchd` jobs so the page is always ≤24h stale. |

---

## Roadmap

### Phase A — Production Azure deployment (PARKED, low effort)

Local stack is the daily driver — everything works end-to-end on
`docker compose up -d`. The deployed Azure + Firebase URLs exist
and CI is wired (`azure-api-deploy.yml`, `firebase-hosting-deploy.yml`)
but three config gaps prevent feature parity with local:

1. **Ingest token** — Azure App Service → Configuration → Application
   settings → add `Ingest__Token` = a long random secret
   (`openssl rand -hex 32`). Mirror in `~/.tradepro/credentials.prod`
   so a Mac-side `tradepro-compare --push` against the prod URL
   actually populates the deployed Compare page. Without this the
   public site shows an empty cache forever.

2. **Trading 212 keys (optional)** — same Azure config page:
   `Trading212__Mode=demo`, `Trading212__ApiKey`, `Trading212__ApiSecret`.
   When set the deployed Portfolio tab + T212 endpoints light up;
   skipped, they return the same `enabled: false` envelope the local
   stack does.

3. **Finnhub key (optional)** — `Finnhub__ApiKey` from finnhub.io free
   tier. Enables forward-earnings warnings on the deployed daily
   digest. No-op until set.

After all three are in place, the deployed UI mirrors local feature-
for-feature. Until then: local-only is the supported path; the public
site renders a stale or empty cache. Reactivate when the local flow
has a stable user pattern worth sharing.

Estimated effort: 30 minutes of Azure Portal clicking + one
`tradepro-compare --push` against the prod URL to populate the cache.
No code changes required — env-var driven.

### Phase D — Data persistence + AWS infra (foundation for everything below)

**The gap:** today everything lives in volatile shapes:
  - Compare payloads → JSON files in `/data/compare` (lost when the
    api container is wiped without the named volume).
  - Fundamentals → fetched fresh on every comparator run; nothing
    historical is preserved.
  - Sentiment scores → ephemeral; same headlines re-scored each cycle.
  - Backtest results → never persisted; user can't compare today's
    backtest to last week's.

  Several upcoming phases (snapshot store for P/E-vs-history per
  spec §10 Q1, portfolio simulation Monte Carlo per Phase B, gem
  hunter recovery tracking per Phase G) all depend on **historical
  series we don't currently retain**. We need a real data layer.

**The proposal:** stand up AWS infra reusing the existing
EnergyCosmos account so we don't need a fresh signup or billing
setup. Three components, layered cheap → expensive:

  **D1 — S3 archive of compare payloads (1 day)**
  - Every successful `tradepro-compare --push` also uploads the
    payload to `s3://tradepro-archive/compare/<universe>/<run_id>.json`
  - Versioned objects + lifecycle policy: keep 90d hot, transition
    to Glacier IR for older
  - Cost: pennies/month at our volume. Unblocks "show me how the
    NVDA verdict changed week-over-week" + audit trail for the
    decision-trace verifier.

  **D2 — Postgres (RDS) for the snapshot store (3 days)**
  - Tables: `fundamentals_snapshots` (symbol, fetched_at, P/E,
    expense_ratio, n_holdings, dividend_yield, …), `analyst_consensus_snapshots`,
    `sentiment_scores` (headline_id, scored_at, sentiment, themes)
  - Cron job from the worker writes a snapshot row each refresh
  - Unblocks the historical-P/E-vs-own-5yr-median lens the spec
    actually wants (currently using basket-relative as a stand-in)
  - RDS db.t4g.micro is ~£10/mo; pause when the engine isn't
    running to halve that

  **D3 — Compute migration: worker → Lambda + EventBridge (3 days)**
  - Replace the docker worker with a Lambda triggered by EventBridge
    every 30 min. Same Python codebase, packaged as a layer
  - Removes the "did you remember to restart the worker container"
    failure mode that plagued the May 2026 sessions
  - Macbook strategy run stays as a local-dev convenience (single
    command, no cloud dependency)

**Why now:** Phase B (portfolio simulation) needs daily price
history retained. Phase G (gem hunter) needs week-over-week
recovery signal tracking. Phase R (risk rating) doesn't strictly
need persistence but benefits from drawdown history. D1 unblocks
all three; D2 unblocks the snapshot store the spec actually needs;
D3 fixes the worker reliability gap once and for all.

**Migration path:** D1 first (zero-risk, pure archive). D2 second
(adds a real query engine without removing anything). D3 last
(only after D1 + D2 are solid). Local docker stack stays as the
dev experience throughout — AWS is the **persistence + scheduled
compute** plane, not a replacement for the local feedback loop.

**Estimated total: 7 days.** Not blocking Phase B (Phase B can run
on existing in-memory data); blocks the historical-P/E lens that
the spec currently has as a parked item.

---

### Phase G — Gem Hunter (deep-value mean-reversion scanner)

**The gap:** TradePro's current BUY signals favour instruments
already in an uptrend (above SMA200, positive 12m momentum). That's
correct for trend-following but blind to **the asymmetric upside
of a quality name beaten down to a real entry**. A FTSE 100 stock
−40% from its 5y peak with intact fundamentals + early RSI recovery
is the canonical "gem" — and today the engine doesn't surface it
because Family-1 strategies all read "below SMA200, weak momentum"
as a sell signal.

**The proposal:** new `find_gems()` scanner that runs after the
existing comparator and identifies instruments matching a separate
profile:

  Required (all of):
  - **Quality intact** — 5y Sharpe ≥ 0.5, max-DD recovery
    historically ≤ 24mo (i.e. it has come back from drawdowns
    before, not a permanent value trap)
  - **Down meaningfully from 5y peak** — drawdown_from_peak_pct
    ≤ −25% (not a 5% pullback, a real correction)
  - **In bottom quartile of 52w range** — range_position_pct ≤ 25
  - **Positive cross-basket valuation flag** — CHEAP per the new
    P/E hybrid lens (P/E for stocks, yield for ETFs)

  At least one of (recovery signal):
  - **RSI bouncing** — RSI ≥ 35 AND rising over last 5 bars
    (mean-reversion entry)
  - **Above SMA200 just turned** — crossed above in last 20 bars
  - **Cross-basket momentum z-score improving** — first positive
    z after a string of negative

  Filters out:
  - Penny stocks / micro-cap (market cap floor)
  - Single-stock concentration risk (passive horizon n_holdings
    handles this for ETFs)
  - Names with sentiment ≤ −0.30 (something's actually broken)

**Output:** new universe `gems_today` with 5-15 candidates per
refresh. Surfaced on the Decide page as a dedicated "Gems" tab
alongside the existing BUY/WAIT/AVOID buckets. Each gem carries
the same horizon classification as everything else — typically
swing WATCH (oversold but not yet confirmed) + long-term BUY
+ passive BUY for ETFs.

**Why it matters for the user's stated goal:** "Find a real Gem" —
the ETF universes already cover S&P 500 / FTSE 100 / sector +
factor ETFs. Gem hunter gives the user the **contrarian** lens on
the same data.

**Estimated effort:** 1 day for the scanner + universe wiring,
half a day for the dashboard tab + email digest section, half a
day for behave scenarios + tooltip / help-content explainer.
Total: 2 days.

---

### Phase R — Risk rating per recommendation

**The gap:** every BUY / WAIT / AVOID + horizon classification +
swing composite tells the user **what to do**. None of them tell
the user **how risky doing it is**. A BUY on USMV (low-volatility
factor ETF) and a BUY on TSLA (40%+ annualised vol, 50%+ drawdown
history) both render as "BUY" in the email — but the position size
the reader should take is wildly different.

**The proposal:** every row gets a `risk_rating` field — LOW /
MEDIUM / HIGH / EXTREME — derived from a transparent rule chain
visible in the decision trace:

  Risk inputs (all sourced from existing fields):
  - **Annualised volatility** (`vol_30d_annual_pct`) — primary
    driver. <15% = LOW, 15-25% = MEDIUM, 25-40% = HIGH, >40% =
    EXTREME.
  - **Max-DD recovery time** — historically slow recoverer
    (>3y) bumps the rating one tier.
  - **Sentiment material-negatives** — ≥3 in last 7d bumps one tier.
  - **Range position** — at 52w highs (≥80th pctile) bumps one tier
    (mean-reversion risk).
  - **Cross-basket dispersion** — symbol's z-score volatility
    relative to peers; outlier names get a tier bump.

  Cap: rating can move at most ±2 tiers from the volatility
  baseline so a tame ETF doesn't end up EXTREME from sentiment alone.

**Surfaced as:**
  - Dashboard: a small pill next to each bucket label (LOW = green,
    MEDIUM = amber, HIGH = red, EXTREME = magenta)
  - Email digest: column added to the BUY/WAIT/AVOID tables
  - PDF: explicit "Risk: HIGH — annualised vol 38%, ≥3y recovery
    historically" line on every per-symbol page
  - MCP: `risk_rating` and `risk_factors` fields on every row so
    Claude can quote them in conversation

**Position-sizing recommendation (optional, Phase R+):** combine
risk rating with portfolio size to suggest a per-position cap:
LOW ≤ 25%, MEDIUM ≤ 15%, HIGH ≤ 8%, EXTREME ≤ 4%. Stays advisory,
honest about being heuristic.

**Why it matters:** the swing composite already mixes quality +
valuation + event + price. Risk rating is **orthogonal** — it
asks "if this BUY is wrong, how bad is the downside?" That's the
question the user has been asking implicitly when they ask "should
I really hold VUKE if it might go to 36p?"

**Estimated effort:** half a day for the rule chain + per-row
attachment, half a day for UI surfacing (dashboard pill + email
column), half a day for behave scenarios + help-content. Total:
1.5 days.

---

### Phase B — Portfolio simulation engine (NEXT MAJOR after Phase X)

**The gap:** TradePro tells you what to BUY / WATCH / AVOID right now,
across three horizons. But it doesn't yet answer **"if I keep this
composition (or actioned all the BUY_MORE / TRIM advice), what's my
likely range of outcomes over 1y / 3y / 5y?"** — and that's the
question every retail allocator actually has to answer before
deploying capital.

This phase adds a `simulate_portfolio()` engine that takes the live
T212 portfolio (or a hypothetical one) and produces an outcome
distribution using established techniques, layered cheap → expensive:

**Layer 1 — Monte Carlo bootstrap (cheap, lands first)**
- Resample N=5000 paths from each holding's historical daily returns
  (block-bootstrap, preserves autocorrelation + fat tails)
- Output: 5th / 50th / 95th percentile equity curves over 1y/3y/5y
- Maximum drawdown distribution; probability of −10/−20/−30% DD
- Sequence-of-returns risk: order of bad years vs good years matters
  more than mean return on accumulation/retirement glide paths

**Layer 2 — Stress backtest (cheap, lands together)**
- Apply 2008 GFC, 2020 COVID, 2022 rate-shock daily returns to the
  CURRENT portfolio weights
- "If we re-ran the GFC starting today, what's your equity drawdown?"
- Compare to the bucket-vote AVOID rate during those windows — does
  the engine catch the bad regime in time?

**Layer 3 — DCA / contribution modelling**
- £X/month contributions over the horizon
- Two policies: DCA into current weights vs DCA into engine-advised
  weights (BUY_MORE → ↑ allocation, TRIM → ↓, HOLD → maintain)
- Cost-basis tracking + tax-lot estimate so the simulator answers
  "how much do I have to put in to hit £Y by 2031?"

**Layer 4 — Mean-variance / risk-parity optimisation (heavier)**
- Markowitz efficient frontier: alternative weight vectors for the
  same target return at lower variance, or higher return at same
  variance — bounded by T212 tradeable instruments
- Risk parity: equal-risk-contribution sizing as a sanity-check baseline
- Black-Litterman extension if we want to inject the engine's views
  (BUY = positive view, AVOID = negative)
- CVaR / Expected Shortfall as the tail-risk metric, not just std-dev

**Layer 5 — Engine-advised portfolio comparison (the headline output)**
- Side-by-side: "your current" vs "engine-advised" vs "S&P 500 / global
  index baseline"
- Same Monte Carlo machinery on each, plot all three on one chart
- Honest framing: this is hypothetical. Past returns ≠ future. Make
  the assumption banner LOUD on the simulation page.

**Open design questions for discussion:**
1. Returns source — yfinance daily returns per holding, or block-
   bootstrap from a regime-aware index? Affects how realistic tail
   events look.
2. Cross-asset correlation — IID resampling underestimates joint
   drawdowns. Need a copula or a "sample whole-day-row from history"
   approach to keep correlations alive.
3. Currency handling — VUKE.L (GBP) and VUSA.L (USD) holdings need
   FX-adjusted returns; do we treat as one ccy at horizon end, or
   surface both?
4. Interface — new Simulations tab "Portfolio Monte Carlo" alongside
   the existing per-symbol backtest, or fold into the Portfolio page
   as a "Project forward" panel?
5. Compute placement — runs in the worker container (Python, has
   numpy / pandas) vs frontend (recharts can render, but 5000 paths
   in JS is wasteful)? Worker-side, push the percentile bands to API.

**Why now:** the horizon classification engine just shipped, so for
the first time the system has **three distinct holding-period
verdicts per symbol**. Simulating a portfolio across those horizons
turns the engine's advice into a concrete probability statement — a
massive uplift over "trust me, BUY this".

**Estimated effort:** 1–2 weeks for layers 1–3 (the headline
deliverable), 2–3 weeks more for layer 4. Layer 5 is presentation,
mostly UI work.

**Memory anchor:** new project memory needed. Tag: portfolio-sim.

---

### Phase X — Multi-family signal stack + swing-trading composite (NEXT MAJOR)

Diagnosis (2026-05-04): every existing strategy (`sma_crossover`,
`macd_signal_cross`, `rsi_mean_reversion`, `donchian_breakout`,
`buy_and_hold`) is a price-vs-its-own-moving-average indicator.
Same family. They agree most of the time, so the bucket vote can't
distinguish "broadly strong basket" from "this one outperforming
peers". To get genuine alpha we need uncorrelated signal families.

| Family | What it asks | Status |
|---|---|---|
| 1. Price / technical | Is price above its own MA? | ✅ 5 strategies + range-position guard (May 2026) |
| 2. Valuation | Is this cheap? | ✅ basket-relative P/E (stocks) + yield (ETFs) hybrid lens. Historical-P/E vs own median still needs snapshot store. |
| 3. Cross-sectional / factor | How does this rank vs peers? | ✅ rank + zscore annotation per row |
| 4. Event-driven | Recent earnings beat + retreat? | ✅ `BEAT_AND_RETREAT` signal + Finnhub forward calendar (May 2026) |
| 5. Macro overlay | What regime are we in? | ✅ `macro_regime.py`: VIX + HYG credit spread + 10Y yield → GREEN/AMBER/RED gate; `size_multiplier()` drives COMPASS + paper engine sizing (May 2026) |
| 6. Sentiment | What's the news saying? | ✅ two-tier demotion: BUY→WAIT at -0.30, any→AVOID at -0.45 |

**Build order:**
1. ✅ Cross-sectional momentum rank (annotation, not yet a verdict driver)
2. ✅ Valuation flag — hybrid P/E (stocks) + yield (ETFs); historical
   median vs own 5y still parked pending snapshot store
3. ✅ Earnings calendar via Finnhub free tier + recent-beat-and-retreat detection
4. ✅ `evaluate_swing(symbol)` composite scorer 0–8 across
   quality / valuation / event / price layers; STRONG_BUY at ≥6
5. ✅ **Horizon Classification Engine** (TRADEPRO-SPEC-001 §6) — splits
   the verdict into swing / long-term / passive horizons so the same
   instrument gets independently-scored advice per holding period
5a. ✅ **COMPASS Model** — 6-factor 0–100 alpha score (momentum 20%,
    earnings revision 20%, sector RS 15%, quality 15%, analyst 15%,
    sentiment 10%, valuation 5%). Macro-gated; wired into every
    compare.py row + CompassMomentum paper strategy. EPS revision
    tracker + signal ledger shipped as supporting infrastructure.
    (May 2026 — Sprint 1 + 2)
6. Tranche-based position sizing: T1=40% now, T2=30% on RSI ≤ 35,
   T3=30% on RSI ≤ 30; max 20% per name; cash sleeve for reserves
7. Exit rules: profit target T1×1.20 (sell 40%), stop T1×0.85 (full),
   trailing 12% off local high once price > T1×1.10

**Why park the big composite:** building `evaluate_swing` right
needs position state (which tranches deployed, cost basis,
days-since-beat per symbol). That's the Phase 2 portfolio-aware
engine territory; ship together as one cohesive feature once T212
positions sync is wired into the rationale layer.

Memory anchors: `project_phase3_multifamily_signals.md`,
`project_phase2_portfolio_aware.md`.

### ✅ Phase 0–3: research + verdict pipeline (DONE)

The platform now answers "today, should I BUY / WAIT / AVOID, and
which ETF" with backtest evidence, regime survival, decision trace,
and analyst cross-check.

### Phase 4.5 — Data model formalisation + UI-editable config

The compare payload has grown organically (rows, regimes, market_state,
fundamentals, news, sentiment, currency, errors, llm). Time to make it
a first-class contract.

- [ ] **Versioned schema**: every payload carries `schema_version`
      (semver). Bumps when fields are removed or semantics change;
      compatible additions don't bump.
- [ ] **Pydantic / dataclass models** in `tradepro_strategies/schema.py`
      replace ad-hoc dicts. The comparator + ingest endpoint validate
      against the same schema. Renders as JSON Schema for free.
- [ ] **Generated TypeScript types**: `npm run gen-types` reads the
      Python schema and writes `frontend/src/api/types.generated.ts`.
      Eliminates the manual TS↔Python drift that already cost us one
      missing-import build break.
- [ ] **Storage shape mirrors API shape**: `FileCompareStore` writes
      the same envelope it returns over HTTP — already true, but lock
      it in by serialising via the schema, not by hand.

### Phase 7 (extended) — UI-editable configuration

The user has called out that thresholds, watchlists, regimes, and fee
preferences should be tunable without a code change. Today they're
all in code. This phase moves them server-side and exposes a Settings
page.

- [ ] **Watchlists**: editable from the UI, persisted in Firestore
      (or the file-backed store as a simpler bridge). Replaces the
      hard-coded `WATCHLISTS` dict.
- [ ] **Sentiment thresholds**: `SENTIMENT_DEMOTION_THRESHOLD` and
      `SENTIMENT_MIN_MATERIAL` editable from the UI. Each compare run
      reads the active values; the payload's `llm.demotion_rule`
      already exposes them so the change is visible.
- [ ] **Regime windows**: add custom regimes from the UI ("Brexit
      vote 2016-06-23 → 2016-07-08").
- [ ] **Fee model**: per-broker presets (Trading212, Freetrade, IBKR
      tiers) editable, default applied by region.
- [ ] **LLM provider/model**: choose via UI (drop-down of installed
      Ollama models + 'use Anthropic API' toggle with key entered in
      Settings).
- [ ] **Custom watchlists from the UI** *(user-stated 2026-05-03)*:
      "create watchlist like any other trading app". Settings page
      gets a Watchlists section with: list existing universes
      (pre-built + user-created), create with a name + symbol
      autocomplete, edit / delete. Server-side store sits next to
      compare cache (`<Compare:StorePath>/watchlists/<name>.json`).
      Mac comparator picks them up by name from the same `--watchlist`
      flag — no code change to add a new universe.
- [ ] **Symbol autocomplete** *(user-stated 2026-05-03)*: any free-
      text symbol input (Signals, Watchlist editor) gets a dropdown
      backed by `/api/marketdata/search` → Yahoo's symbol search
      endpoint. Filters by ticker + company name; preview shows
      currency / venue. Stops the "I typed NV instead of NVDA"
      class of error; today that returns a 500 (now hardened to a
      graceful 'no data' but autocomplete prevents the trip
      entirely).

### Phase 4 — Robust ETF execution (IN PROGRESS)

The output is correct. The plumbing isn't yet trustworthy enough for
a daily decision tool.

- [x] **Persistence**: `FileCompareStore` reads/writes
      `<Compare:StorePath>/<universe>.json` (default `/data/compare`
      in containers, named-volume in compose). Atomic writes
      (tmp + rename + fsync). Hydrates from disk on startup.
      Survives restarts and deploys.
- [x] **Scheduled refresh**: `strategies/scripts/refresh.sh` +
      `com.tradepro.refresh.plist` + `install-launchd.sh`. One-shot
      install runs every ETF universe daily at 22:30 UTC, logs to
      `~/.tradepro/logs/refresh-<date>.log`, exits non-zero on any
      failure.
- [x] **Stale-data banner**: traffic light on `/compare` —
      green ●&nbsp;Live <24h, amber ●&nbsp;Stale 24-72h with the exact
      `tradepro-compare` command shown, red ●&nbsp;Very stale >72h
      with a "refresh before deciding" warning.
- [x] **Per-symbol fetch error reporting**: comparator emits a
      top-level `errors` array (symbols that failed to fetch or
      came back empty); each row carries `data_age_days` so a stale
      price gets a visible 'Nd stale' badge in the matrix instead
      of silently feeding the rule chain.
- [x] **Currency awareness**: each row carries the venue-derived
      currency (`.L`→GBP, `.DE`→EUR, default USD). The payload
      flags mixed-currency runs (`currency_mix.is_mixed`); the UI
      shows a per-row Ccy column + a warning above the matrix
      when the universe spans more than one currency.
- [x] **Health/details endpoint + page**: `/health/details`
      returns API status, Mac heartbeat liveness, and per-universe
      cache freshness in one payload. New /health page polls every
      30s and shows a single 'is the system OK?' verdict (ok /
      warn / needs_attention) with three cards underneath.
- [ ] **Run history page**: list past comparator runs with
      `run_id`, timestamp, universe, row count, strategies, status.
      Click a `run_id` → view the full event log (the JSONL
      emitted by `RunLogger`) and the manifest. **Deferred** —
      requires keeping multiple runs per universe in the store
      (currently latest-only).
- [ ] **Per-decision audit trail**: from a Compare row, click "why
      this verdict" → land on a page with every input + every rule
      that ran, with a permalink stable across re-runs. **Deferred**
      — depends on run history.
- [ ] **Correlation-ID logs**: structured backend logs threading a
      correlation ID per ingest request through every log line.
      **Deferred** — invisible to users without log access; lower
      priority than the visible Phase 4 items above.
- [ ] **Verbose run logging on the Mac side**: every comparator run
      should emit an event JSONL with per-symbol fetch latency,
      LLM-call latency + cache hit/miss, sentiment-scoring totals,
      and failure reasons — already partial, formalise into the
      `RunLogger` so the run history page can render a full
      timeline. Pairs with traceability above.

### Phase 5a — ETF fundamentals + market news (DONE part 1)

- [x] **ETF fundamentals** from Yahoo's quote summary: dividend
      yield, expense ratio, AUM, top-10 holdings with weights,
      sector mix, inception date. Bond ETFs additionally surface
      yield-to-maturity + duration. Rendered in the Compare expand
      panel.
- [x] **Per-symbol news headlines** from Yahoo's news feed
      (`yfinance.Ticker.news`). Top 5 in the expand panel —
      clickable to source, publisher + relative timestamp. No
      sentiment scoring yet (Phase 6).
- [ ] **Manual news flag**: operator can mark a news item as
      "material" (e.g., earnings beat, guidance cut) so it
      influences the verdict. Belongs after Phase 6's automatic
      sentiment so manual flags only override the model when the
      operator disagrees with it — not the primary input.

### Phase 5c — Information sources expansion (live + historical news + uploads)

User-stated requirement (2026-05-02): the platform must factor in
live news, historical news, financial reports, and user-uploaded
documents into its decisions. Today's news pull is a snapshot from
Yahoo's per-symbol feed; this phase makes the information layer
first-class and extensible. **No-hallucination contract still
applies** — anything fed in here gets cited like any other source,
and an LLM rationale that references it must verify against it.

#### 5c-i — Live news + price feeds (real-time signal)

User-stated (2026-05-03): "got Trading212 application access. We can
get real live data which will be up-to-date then Yahoo." T212's
public API exposes portfolio + orders + instruments-search but NOT
streaming quotes — for live prices we still need a streaming
provider. Two-track plan:

- [x] **Trading212 config + status probe** *(2026-04-28)*: `Trading212Options`
      bound from config (`Mode=disabled|demo|live`, key/secret), typed
      `Trading212Client` with Basic auth + rate-limit headers, and
      `GET /api/integrations/trading212/status` to confirm a key pair
      reaches the broker. Off by default; live spec at
      `strategies/docs/api.json`. Important: T212 has **no OHLC** — Yahoo
      stays for prices.
- [ ] **Trading212 portfolio sync**: `/equity/positions`,
      `/equity/account/summary`. Read-only first. Used for paper-trading
      reconciliation in Phase 8 + 'highlight what you actually own'
      badge on the Compare page.
- [ ] **Trading212 instruments registry**: `/equity/metadata/instruments`
      gives the full T212 universe (tickers, currencies, venues, ISINs,
      tradeability flags). Becomes the autocomplete source for the
      symbol picker (Phase 7).
- [ ] **Live price stream** *(separate from T212)*: Alpaca free tier
      (US equities, IEX-only, 200 req/min) OR IBKR Gateway delayed
      quotes via `ib_insync`. WebSocket for active symbols, fallback
      to REST polling. Bucket assignments refresh on tick rather
      than once per day. **Opt-in per universe** so free-tier users
      pay nothing.
- [ ] **RSS / Atom ingestion**: per-watchlist feed registry. Defaults:
      Yahoo per-symbol news (already pulling), Reuters/AP/FT business
      RSS, sector-specific feeds (FT Markets, Bloomberg open RSS).
- [ ] **Polling worker**: Mac-side scheduled job (launchd) that
      checks each feed every N minutes; deduplicates by URL hash;
      writes new items to a local store keyed by (symbol, fetched_at).
- [ ] **Webhook receiver** *(optional, paid feeds)*: a small endpoint
      on the API that accepts pushed news items from a service like
      Polygon, Finnhub, or a custom Comet task. Same auth pattern as
      `/api/ingest/compare`.
- [ ] **Rate-of-arrival signal**: when a symbol's news rate spikes
      (e.g. 5× the 30-day average) the comparator surfaces a "news
      surge" flag on the row. Material event detector — surfaces
      *before* the LLM reads anything.

#### 5c-ii — Historical news archive

- [ ] **Cumulative store** of every fetched news item, keyed by
      (symbol, published_at, source). Survives restarts; queryable
      by date range. Lives at `~/.tradepro/news/<symbol>.parquet`
      (one Parquet per symbol, append-only, compressed).
- [ ] **Backfill from Yahoo `Ticker.news` history** + free tier
      providers (NewsAPI free tier, GDELT) — best-effort; gaps in
      coverage are fine and tagged as such.
- [ ] **Historical-context retrieval at decision time**: for a given
      verdict, surface "the last 5 BUY signals on this symbol came
      with these headlines" — connects the rationale to past
      precedents.

#### 5c-iii — Document upload (PDF / HTML / TXT)

The thing that makes the platform research-aware. User uploads a
prospectus, an analyst report, a press release; the system extracts
text, chunks, embeds, and retrieves at decision time.

- [x] **Mac-side extractor** (`tradepro_strategies.documents`):
      pdfplumber for PDFs (per-page sections), trafilatura for
      HTML (boilerplate-stripped), pass-through for TXT / MD.
      Returns ExtractedDocument with sha256 + char_count +
      structured sections.
- [x] **Ingest CLI**: `tradepro-doc-upload <file> --symbols QQQ,VOO
      --title "..."` extracts locally, builds a manifest, pushes
      via the existing `/api/ingest/document` token-auth endpoint.
      Raw files stay on the Mac — only structured text + manifest
      cross the wire.
- [x] **API document store + endpoints**: `FileDocumentStore`
      mirrors `FileCompareStore` (file-backed, atomic-rename,
      hydrates on startup). Read endpoints:
      `GET /api/documents` (list, optional ?symbol= filter),
      `GET /api/documents/{id}` (full envelope),
      `GET /api/documents/{id}/text` (extracted text — used by the
      Mac comparator at decision time).
- [x] **Behave coverage**: features/documents.feature verifies
      extraction shape, manifest building (uppercase symbols, uuid
      doc_id), and rejection of unsupported extensions.
- [ ] **Frontend `/documents` page**:
  - Drag-and-drop upload (forwards to /api/ingest/document via the
    Mac CLI initially; native browser upload in a follow-up that
    pipes raw bytes to the Mac for extraction)
  - List of uploaded docs with title, size, linked symbols, date
  - Click to view extracted text + which decisions it has been
    retrieved into
- [x] **Chunking**: section-aware sliding window
      (`tradepro_strategies/embeddings/chunker.py`). Default
      ~500-token windows / ~100-token overlap, never crosses
      section boundaries (PDF page = section). Stable
      `chunk_id = sha1(doc_id, section, chunk_idx, prefix)` so
      re-chunking is idempotent and embeddings only re-run when
      content actually changes.
- [x] **Embeddings**: `OllamaEmbedder` (default
      `mxbai-embed-large`, 1024-dim, ~700 MB). Override via
      `TRADEPRO_EMBED_MODEL`. Failures (model not pulled / Ollama
      down) surface visibly via `embed.failed` events, never
      crash the comparator.
- [x] **Vector store**: Parquet at
      `~/.tradepro/cache/embeddings.parquet`, brute-force cosine
      similarity in numpy. Plenty for single-user volume (dozens
      of docs, hundreds of chunks); upgrade to DuckDB-VSS or
      sqlite-vec when we cross ~10k chunks.
- [x] **Retrieval at decision time**: comparator calls
      `update_embeddings()` at run start (catches up new docs),
      then per-symbol queries the store for top-5 chunks with
      score ≥ 0. Chunks land in the rationale's
      `retrieved_evidence` block; the LLM treats them as allowed
      facts subject to the same verifier (claims unsupported by
      either structured row OR retrieved chunk text → template
      fallback).
- [x] **Citation URI scheme**: every chunk has
      `tradepro://documents/<doc_id>#chunk-<chunk_id>` — stable,
      cite-able, and surfaced on every Rationale.retrieved_chunks
      entry so the frontend can deep-link the source.

#### 5c-iv — Document discovery (automated)

- [ ] **Edgar (SEC) feed**: ingest 10-K, 10-Q, 8-K filings for
      tickers in any watchlist automatically. SEC's full-text
      search is free.
- [ ] **Earnings transcripts**: pull from Yahoo's earnings tab
      (free) where available; otherwise prompt user to upload.
- [ ] **Per-ETF prospectus auto-fetch**: for each ETF in a
      watchlist, follow the Yahoo `summaryProfile.fundFamily` →
      issuer URL → prospectus PDF link. One-off per ETF; doc is
      cached forever.

#### 5c-v — Verifier integration with retrieved evidence

When the rationale builder pulls in retrieved chunks as facts, the
verifier MUST be able to follow citations back to the chunk's text.
This means:
- [ ] Every chunk has a stable URI (`tradepro://documents/<doc_id>
      #chunk-<n>`) that the LLM cites.
- [ ] The verifier compares claims against the *concatenated* fact
      bundle (structured row data + retrieved text). If a citation
      doesn't trace, the rationale is rejected (template fallback
      kicks in, same contract as today).

### Phase 5b — Extending to individual stocks (gated on ETF being solid)

ETFs answer "which basket should I own". Individual stocks need a
different signal set: fundamental ratios + earnings narrative
matter, not just price action. This phase **starts only after Phase
4 + 5a leftovers are clean** for ETFs — we don't fork the focus
mid-ETF.

- [ ] **Fundamental ratios per stock** from `Ticker.info` /
      `.financials` / `.balance_sheet` / `.cashflow`: P/E,
      forward P/E, PEG, P/B, ROE, debt/equity, free-cash-flow
      yield, revenue growth (3y / 5y), gross / operating / net
      margins, margin trend. All free, daily refresh, deterministic.
- [ ] **Per-stock decision_trace checks**: "P/E vs sector median",
      "ROE > 15% (quality)", "FCF yield > 5% (cheap)",
      "Debt/equity < 1 (balance-sheet clean)". Plug into the
      existing `_classify` rule chain — same transparency contract.
- [ ] **Stock-specific Compare page** (or universe-aware mode on
      the existing one): show ratios alongside CAGR / Sharpe in
      the matrix. Top holdings is replaced by sector + competitor
      peers.
- [ ] **Stock watchlists**: keep the existing `uk_ftse100_sample`
      and `us_megacap_sample`; add curated thematic lists
      (defensives, dividend aristocrats, UK quality compounders).
- [ ] **Cross-asset universe**: an option to compare a target
      stock vs the ETF that holds it ("VOO vs MSFT") so a user
      can decide whether to pick the company or just buy the
      basket.

### Phase 6 — LLM-derived qualitative signals

**Most of Phase 6 applies to ETFs too — not just stocks.** Only
Phase 6b (earnings/10-K narrative analysis) is stock-only by
nature. Sentiment, macro/sector narrative, prospectus summaries,
rationale rewrite, and Q&A chat all apply directly to the ETF
flow and don't depend on Phase 5b shipping first.

| Sub-phase | ETFs | Stocks | Description |
|---|---|---|---|
| 6a — News sentiment | ✅ | ✅ | The Iran-war / tariff-shock demotion case |
| 6b — Earnings / 10-K analysis | ❌ | ✅ | Depends on Phase 5b |
| 6c — Decision rationale rewrite | ✅ | ✅ | Plain-English summary of `decision_trace` |
| 6d — Q&A chat | ✅ | ✅ | "Is GLD a good hedge?", "VWRP vs VUSA?" |
| 6e — Macro / sector narrative | ✅ | ✅ | Weekly 200-word digest per universe / sector |
| 6f — ETF prospectus summary | ✅ | — | Methodology, top weights, rebalance rules |
| 6g — Holdings-aware ETF news | ✅ | — | "What's affecting QQQ this week" via its top 5 |

Strict principle: **the LLM produces context and explanation, never
the verdict.** The decision stays rule-based and transparent;
otherwise we throw away every transparency win we've built. LLM
outputs go in as additional `decision_trace` checks (with their own
status), not as overrides.

#### Phase 6 architecture

- [ ] **`LlmProvider` abstraction** in a new `strategies/
      tradepro_strategies/llm/` module. Implementations:
  - `OllamaProvider` — local M-series MPS, free, private, default
    for high-frequency / low-stakes tasks.
  - `ClaudeProvider` — Anthropic API, used for high-value /
    nuanced tasks where 8B-parameter models fall short. ~£1-5/mo
    at single-user volume.
- [ ] Every LLM call has a strict JSON output schema; parse
      failures → `null` / "no signal" rather than crashing the
      run. Outputs are cached by `hash(prompt + input)` so we
      never re-score the same headline twice.
- [ ] Telemetry: tokens-in, tokens-out, latency, parse-success
      rate per task — exposed on `/api/health/llm` for the same
      observability story as the worker badge.

#### Phase 6a — News sentiment (lowest stakes, highest frequency)

- [ ] Local Ollama scores each headline already in the news
      pull: `{sentiment: -1..+1, themes: [string], material: bool}`.
- [ ] Aggregate to a **7-day rolling sentiment trend per symbol**.
      Add a new check to `decision_trace`:
      "Sentiment trend (7d)" → pass / warn / fail.
- [ ] **Bucket demotion rule**: BUY → WAIT when sentiment trend is
      sharply negative AND material headlines are present. The
      Iran-war / tariff-shock case lands here.
- [ ] **Bias guard**: never *promote* a verdict on positive
      sentiment alone — the rule-based check has to also pass.

#### Phase 6b — Earnings call + 10-K narrative analysis (stock-only)

Depends on Phase 5b. Likely uses Claude API (Anthropic) — the
analysis is more nuanced than 8B Ollama can reliably handle.

- [ ] Ingest earnings-call transcripts and 10-K MD&A / risk-factors
      sections. Source: SEC EDGAR (free), Yahoo earnings tab, or
      paid transcript provider later.
- [ ] LLM extracts a structured tag set:
      `{guidance_direction, management_emphasis: [string],
      new_risks: [string], segment_highlights: [string],
      tone: bullish|neutral|cautious}`.
- [ ] Render a "Fundamental narrative" panel on the stock
      detail page. Tags are surfaced as filter chips so a user
      can scan for "guidance cut" across the watchlist.

#### Phase 6c — Decision rationale rewrite

- [ ] Take the existing rule-based `decision_trace` and an LLM
      writes a 1-paragraph plain-English summary
      ("QQQ is BUY because price is healthily uptrending, RSI is
      not overbought, and 4 of 5 strategies are currently long.
      The tech-heavy tilt does mean it's the most exposed to a
      rate shock — see GFC -55%, 2022 -25% in the regime panel.").
- [ ] Cached aggressively — the rationale only re-generates when
      the rule outputs actually change.

#### Phase 6d — Q&A chat as an MCP server (Anthropic's Model Context Protocol)

User insight (2026-04-30): the Q&A use-case is **literally the MCP
server pattern** — "I ask a question, a server with tools and
resources runs simulations / fetches data / formats the answer via
an LLM". MCP standardises exactly this shape; using it gives us a
free integration with Claude Desktop, Cursor, and any future
MCP-aware client without a custom chat protocol.

- [ ] **`tradepro-mcp` server** — a small Python service using the
      official `mcp` SDK (or `fastmcp`). Exposes the platform as
      tools + resources + prompts:
  - **Resources** (read-only):
    - `tradepro://compare/{universe}` — latest ranked-comparison
      payload (the JSON we already store).
    - `tradepro://watchlists` — defined universes.
    - `tradepro://regimes` — the 13 historical stress windows.
    - `tradepro://settings` — current sentiment thresholds + rule
      configuration (Phase 7 / 4.5 territory).
  - **Tools** (the LLM can invoke):
    - `run_comparison(universe, strategies?)` — kick a fresh run
      on the Mac, push, return the new payload.
    - `get_market_state(symbol)` — price, RSI, SMA, drawdown,
      decision_trace.
    - `get_regime_history(symbol, strategy)` — per-regime stats.
    - `get_news_with_sentiment(symbol)` — news + LLM scores.
    - `get_health()` — system + worker status.
  - **Prompts** (one-click templates):
    - `analyse_etf(symbol)` — "Should I invest in {symbol}? Use
      compare data, regime history, and current news to answer."
    - `compare_etfs(symbols)` — side-by-side analysis.
- [ ] **Strict-decision contract** *(unchanged)*: the LLM in the
      MCP client (Claude Desktop or our own chat UI) is allowed
      to summarise + cite + explain. The actual BUY/SELL/HOLD
      verdict comes from the rule-based payload — the MCP tools
      *return* it, the LLM doesn't compute it. Citations make
      this auditable: every claim is traceable to a tool call.
- [ ] **Two consumers** out of the box:
  1. **Claude Desktop** integration — drop a config in `~/Library/
     Application Support/Claude/claude_desktop_config.json` and
     ask questions of your own portfolio.
  2. **In-app `/chat` page** — same MCP server, accessed via a
     thin frontend chat using either Claude API or local Ollama
     as the conversational LLM (the MCP server is provider-
     agnostic).
- [ ] Telemetry: every MCP tool invocation gets a structured event
      in the run log so the same observability story applies.

#### Phase 6e — Macro / sector narrative (ETF + stock)

- [ ] Weekly 200-word LLM-generated digest per ETF universe and
      per sector ("what's going on in tech / fixed income /
      emerging markets this week"). Inputs: macro context bar,
      regime overlap, recent news headlines, sector returns.
- [ ] Surfaced at the top of `/compare` as a context paragraph
      below the macro context bar — the qualitative companion to
      the quantitative VIX / 10Y / S&P-drawdown numbers.
- [ ] Refresh weekly; cached aggressively.

#### Phase 6f — ETF prospectus summary

- [ ] Pull the official prospectus (PDF or methodology page) for
      each ETF in the universe.
- [ ] LLM extracts: index it tracks, rebalance cadence, top
      country / sector / factor weights, replication method
      (physical vs synthetic), holdings concentration (% in top
      10), securities lending policy, ESG screens if any.
- [ ] Render in the expand panel as a one-paragraph "what's in
      this ETF" summary above the holdings list. Beats reading 80
      pages — a beginner can grok the structural exposure in 30
      seconds.
- [ ] Cached for the lifetime of the prospectus version (months).

#### Phase 6g — Holdings-aware ETF news synthesis

The bridge between ETF-level analysis and the stock-level news +
fundamentals pipeline (Phase 5b + 6b).

- [ ] For each ETF in a universe, take the news + sentiment of
      its top 5-10 holdings (already pulled in Phase 5/5b).
- [ ] LLM synthesises into a single "what's affecting QQQ this
      week" paragraph — weighted by holding weight, focused on
      material news.
- [ ] Surfaced in the expand panel as "Underlying news synthesis".
      An ETF investor sees the company-level signals without
      drilling into 10 different stocks.

### Phase 7 — Live signals + alerts (incl. optional real-time feed)

- [ ] Daily signal evaluation across all watchlists.
- [ ] Email / push (Firebase Cloud Messaging) when a row's verdict
      changes (e.g., BUY → WAIT).
- [ ] Per-user notification rules ("only alert me on BUY in
      `etf_uk_core`").
- [ ] **Real-time / intraday feed (opt-in)**. Cheapest credible
      providers:
  - Alpaca: free tier, 200 calls/min, IEX-only quotes (US equities).
  - IBKR Gateway: delayed (free with account) or real-time (per-
      exchange sub) via `ib_insync` from the Mac.
  - Polygon: starter tier ~$30/mo for end-of-day-with-snapshots.
  Strategy: keep daily bars as the universe-wide truth, layer a
  per-symbol streaming source on top for "live" mode on the symbols
  a user actually holds. Bucket assignments refresh on tick instead
  of once per day. Stays opt-in to keep free-tier costs at zero
  for users who don't need it.

### Phase 8 — Paper trading + journal

- [ ] In-app portfolio ledger (no broker integration yet).
- [ ] When the user clicks BUY on a card, log a paper trade with the
      exact `run_id` it came from — full traceability from decision
      back to the data that supported it.
- [ ] P&L roll-up per strategy and per regime.

### Phase 9 — Broker integration (read-only first)

- [ ] Read-only broker connection (Trading212 / Freetrade / IBKR).
- [ ] Reconcile real holdings against paper positions.
- [ ] Manual order placement with confirmation guardrails.
- [ ] Audit log of every order and the signal that led to it.

### Phase 10 — Production hardening

- [ ] Real OpenTelemetry → Azure Monitor (or AWS CloudWatch post-
      migration). Trace ID propagated from frontend → API → Python
      worker.
- [ ] AWS migration (see A10). Code is already portable.
- [ ] Cost dashboard: a small `/api/health/cost` endpoint that
      reports current usage vs free-tier limits.

---

## Non-goals (still — for now)

- Real money execution before Phase 9.
- HFT / sub-second strategies.
- Multi-tenant SaaS.
- Regulated investment advice (we're a decision aid, with the
  disclaimer in the UI).

---

## Cost stack (current — AWS personal account)

Migrated off Azure + Firestore in May 2026. The system now runs on a
single small EC2 host and an EBS volume; everything else is free-
tier or per-call.

| Layer | Service | Notes |
|---|---|---|
| UI hosting | Firebase Hosting (Spark) | 10 GB bandwidth / mo |
| API + worker host | EC2 t4g.small (eu-west-2) | ~£3-4/mo with auto-stop overnight, ~£1.30/mo idle on EBS |
| Auth | Firebase Auth (Spark) | free for single user |
| Secrets | AWS Secrets Manager (`tradepro/all`) | £0.30/mo per secret |
| State store today | In-memory + JSON files on EBS | wiped by every redeploy — see Phase 5 below |
| Heavy compute | The M-series Mac | electricity only |

**Phase 5 — Postgres migration** (planned, not yet shipped): replace
in-memory stores (`IPendingOrdersStore`, `IPaperSnapshotStore`,
`IPaperBacktestStore`) and JSON-file stores (`FileCompareStore`,
`FileSettingsStore`, `FileDocumentStore`, `InMemoryWatchlistStore`)
with Postgres on the same EC2 host (or RDS db.t4g.micro at ~£10/mo).
Required for prod because every redeploy currently wipes pending
orders, snapshots, settings, and watchlists. Tracked in
[ARCHITECTURE.md](docs/ARCHITECTURE.md) §Stores.

---

## UK-specific defaults

- Currency GBP; UK 0.5% stamp duty on LSE main-market shares (AIM /
  ETFs exempt — `--stamp-duty 0`).
- Yahoo `.L` suffix (`BARC.L`, `LLOY.L`); `^FTSE` / `^FTMC` indices.
- Tax wrapper (Phase 8): ISA mode = no CGT on sell side, £20k/yr
  contribution cap.
- Brokers (Phase 9): Trading212 / Freetrade / Hargreaves Lansdown.

---

## Push pipeline

```
 ┌───────────────┐   POST /api/ingest/<kind>   ┌────────────────┐
 │  Mac (Python) │ ──────────────────────────▶ │ Azure / AWS    │
 │  scheduled    │   Authorization: Bearer     │ .NET 8 API     │
 │  launchd      │                             └───────┬────────┘
 └───────────────┘                                     │ GET /api/compare/...
                                                       ▼
                                             ┌──────────────────┐
                                             │ Firebase Hosting │
                                             │ React UI         │
                                             └──────────────────┘
```

- Auth: ingest token (single static value) on `/api/ingest/*`,
  Firebase ID token on everything else.
- Secrets bundle: **AWS Secrets Manager** at `tradepro/all` in
  eu-north-1 (JSON key/value blob). Both the Mac engine and the EC2
  API read it via SDK at boot. Env vars + appsettings still override
  for local dev. The Mac fallback `~/.tradepro/credentials` is kept
  as a tertiary path for backwards compatibility but new secrets go
  to SM, not to that file.
