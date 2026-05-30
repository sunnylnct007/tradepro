# Current backtest + simulation limitations

> **Read this before trusting any backtest number TradePro produces.**
>
> This document is the honest, trader-readable answer to "what can I
> believe about TradePro's historical evidence?". Every limitation
> below is real today; each one carries a severity, a concrete
> consequence, and the remedy that's on the roadmap.
>
> A trustworthy system tells you what it doesn't know. That's the
> spirit here.

Last reviewed: 2026-05-29
Companion: `ROADMAP.md` → "Phase: Trustworthy data layer"
Visible in UI: Settings → Data Health & Provider Preferences

---

## TL;DR

| Strategy type | Backtest trust level | Why |
|---|---|---|
| **Daily strategies** (`ichimoku_equity`, daily Ichimoku scorer) | ✅ **Roughly trustworthy** within ~1–2% | Yahoo daily back to ~2000 is stable + adjusted close is clean |
| **Intraday strategies** (`intraday_flat`, `orb`, `vwap_mean_reversion`, `bollinger_bounce`, `ma_crossover`) at 1m resolution | ❌ **Effectively fictional past 7 days** | Yahoo's 1-minute history ceiling is 7 days; no other free provider in the chain |
| **FX strategies** (`ichimoku_fx_mr`) at hourly resolution | ⚠️ **Partial** | Yahoo hourly works but FX providers disagree on the same pair by basis points |
| **Slippage assumptions everywhere** | ❌ **Universally optimistic** | All backtests fill at OHLC close. No bid/ask history. Reality: 1bp on liquid ETFs (small), 5–15bp on single names (material). |
| **Live-vs-backtest fill comparison** | ❌ **Impossible** | No historical bid/ask means no way to compare what a backtest predicted vs what IG/T212 actually filled |
| **Strict reproducibility** | ❌ **Weak across all** | yfinance silently revises historical bars; two runs a month apart can disagree by a few bps |
| **LLM gate in any historical backtest** | ❌ **Anachronistic** | The gate fetches CURRENT news and applies CURRENT sentiment to historical entries. Disabling it for backtest creates an honesty gap against live; enabling it creates a different lie. |

If you take one thing from this doc: **your intraday strategies have
never been backtested at meaningful historical depth. They have only
been forward-tested for ≤ 7 days at a time.** That's a hard ceiling
imposed by free-tier data, not a TradePro choice — but it's a real
constraint on what you can claim.

---

## Detailed limitations

### L1 — Intraday data ceiling (CRITICAL)

**Severity**: CRITICAL — limits the evidence layer of every intraday strategy.

**What's true**:
- Yahoo Finance 1-minute history is capped at the last 7 days.
- Yahoo 5m/15m/30m: capped at 60 days.
- Yahoo 1h: many years available.
- IG `/prices/{epic}` has multi-year history but demo accounts have a weekly bar-download allowance (~10k datapoints/week typical).
- No provider currently configured supplies 1m bars older than 7 days.

**Consequence**:
- `intraday_flat`, `orb`, `vwap_mean_reversion`, `bollinger_bounce`, `ma_crossover` cannot be backtested at 1m resolution over any historical window beyond a week.
- What looks like a "backtest" of these in the cockpit is actually a 7-day forward test labelled as a backtest.
- Walk-forward / Monte Carlo / regime-shift stress tests at 1m resolution are not currently runnable.
- The `BUY/SELL/HOLD evidenced by backtest` half of the project north-star is **not satisfied for intraday signals**.

**Remedy (on the roadmap)**:
- Phase B: build the asset-class-pluggable bar cache + provider chain (yfinance → IG `/prices` → finnhub → Polygon flat-files when subscribed).
- Phase C: backfill CLI + UI button to populate the cache for any symbol × date range.
- Phase E: refuse to run a backtest whose data is incomplete, with a clear remediation message.

**Mitigation today**:
- Run intraday backtests only over the last 7 days, and label results "Forward test (7d)" rather than "Backtest".
- For longer horizon analysis, use daily-resampled bars; accept the loss of intraday granularity.

---

### L2 — Slippage is fictional (HIGH)

**Severity**: HIGH — meaningfully biases reported returns upward.

**What's true**:
- All backtests assume fills at the bar's `close` price.
- No historical bid/ask spread is stored anywhere.
- IG / T212 supply bid/ask only at the moment of order placement, not historically.

**Consequence**:
- Backtest returns are systematically optimistic by the realised spread cost.
- Magnitude:
  - Liquid US ETFs (SPY, QQQ, IWM): ~1bp per round-trip. Small but non-zero.
  - Liquid single names (AAPL, MSFT, GOOG): ~3–5bp. Becomes material across many trades.
  - Less-liquid names (small-caps, foreign ADRs): 10–50bp. Can flip a strategy's sign.
  - Wider on 1-minute bars vs daily because intraday spreads are wider than EOD reference.

**Remedy**:
- Phase F (post-data-layer): store IG L1 bid/ask snapshot at every fill; build an empirical slippage model from realised vs theoretical fills.
- Phase G: ship a simple "spread haircut" model (% of mid) as an opt-in backtest mode so analysts can see "what does the backtest look like with 2bp/4bp/10bp spread applied?".

**Mitigation today**:
- Apply a manual haircut when comparing backtest results to live: subtract 1bp for ETF backtests, 5bp for single-name backtests, more for illiquid.
- Treat any backtest Sharpe within ~0.3 of the threshold-of-interest with skepticism — that's well within the slippage error bar.

---

### L3 — LLM gate is anachronistic in backtests (HIGH)

**Severity**: HIGH — backtest and live are not the same system.

**What's true**:
- `LLMSignalGate.evaluate(symbol, ...)` fetches today's news headlines and scores today's sentiment, then returns APPROVED/VETOED/APPROVED_BOOSTED.
- A backtest replaying a 2024 entry would call the gate today; the gate would fetch news from today; the decision would be based on today's sentiment, not the sentiment that existed at the historical entry moment.
- Two compensating choices, both wrong:
  - **Gate enabled in backtest** → historical entries are evaluated against modern news. Backtest is a lie.
  - **Gate disabled in backtest** → live behaviour includes a gate effect that the backtest doesn't capture. Live diverges from backtest by an unknown amount.

**Consequence**:
- No correct way to backtest the LLM gate's contribution.
- Going forward: every live LLM call now persists to `llm_evaluations` (migration 022), so prospective replay becomes possible eventually.
- Retroactively: impossible. The news + sentiment for 2024 entries was never stored.

**Remedy**:
- Phase H: backtest mode for LLM gate replays the stored `llm_evaluations` rows by timestamp — honest from the date `llm_evaluations` started capturing data (≈ 2026-05-28).
- Phase H+1: cohort analyses splitting "backtest with LLM gate disabled" vs "live with LLM gate active" to measure the gate's contribution empirically.

**Mitigation today**:
- Backtests of `ichimoku_equity` and `ichimoku_fx_mr` are run with `LLMGateConfig.enabled=False`. The cockpit must show that flag clearly on every backtest result.
- For live trading, expect a delta from the backtest number equal to whatever the gate vetos / boosts in practice — currently unmeasured.

---

### L4 — Reproducibility is weak (MEDIUM)

**Severity**: MEDIUM — undermines walk-forward + Monte Carlo confidence.

**What's true**:
- yfinance silently revises historical bars (corporate actions, late dividend reinvestments, split adjustments).
- Two backtest runs of the same strategy on the same period, executed weeks apart, can disagree by a few bps.
- No backtest result currently records the data state it used.

**Consequence**:
- Can't reliably compare a backtest result from 2024 with one from today.
- Walk-forward sweeps that depend on stable historical data have a noise floor higher than zero.
- A team member running the same backtest as another team member can get different numbers; the diff isn't a bug, it's data drift.

**Remedy**:
- Phase D: stamp every backtest result with `data_provider`, `data_provider_version`, `bar_count_per_symbol`, `bar_partition_hash`. Reruns with matching hashes MUST produce identical numbers.
- Phase D+1: result viewer surfaces the data hash; "this matches the 2024-08 run" vs "this used different bars".

**Mitigation today**:
- For a definitive comparison, rerun both legs of a comparison on the same day with the same cache state.
- Treat backtest "trends over months" with appropriate humility: the data underneath has been silently rewritten.

---

### L5 — No fill-quality audit (MEDIUM)

**Severity**: MEDIUM — live divergence has no diagnostic.

**What's true**:
- When `intraday_flat` (or any strategy) fills on IG / T212, we record the broker's fill price.
- We do not record the bid/ask at the moment of fill, the broker's depth ladder, the time-to-fill, or the slippage vs mid.
- So "did we get a fair fill?" is unanswerable from data.

**Consequence**:
- A broker fill at a noticeably worse price than mid goes undetected.
- Strategies that look broken in live could be data-layer broken (we filled poorly), not signal-layer broken.

**Remedy**:
- Phase F: store IG L1 snapshot at every fill (new column on `oms_fills`).
- Phase F+1: per-fill spread/slippage analytics in the cockpit.

**Mitigation today**:
- Spot-check a sample of fills against the IG demo UI for the same symbol/time, manually compare to mid.

---

### L6 — Yahoo rate limits silently corrupt sweeps (MEDIUM)

**Severity**: MEDIUM — a backtest sweep over many symbols can have silent holes.

**What's true**:
- yfinance returns HTTP 429 or empty frames when rate-limited.
- The current strategy code treats an empty frame as "no signal", not "data missing".
- A cross-sectional backtest over 100+ symbols at 1m resolution will often have a non-trivial fraction of symbols come back empty.

**Consequence**:
- Symbol X's "no signal today" might mean "real no signal" or "yfinance rate-limited" — we don't distinguish.
- The cross-sectional ranking is biased toward symbols that fetched OK.

**Remedy**:
- Phase A: per-fetch telemetry distinguishes 429 / empty / parse-error / network-error / OK.
- Phase B: cache layer absorbs the rate limits — same-symbol same-window only hits the network once.

**Mitigation today**:
- Run sweeps during off-peak hours (early UTC).
- After any cross-sectional run, sanity-check sample symbols against a manual yfinance call.

---

### L7 — DST / holiday boundaries (LOW)

**Severity**: LOW — but creates occasional off-by-one in intraday entries.

**What's true**:
- US DST shifts in March and November shift the UTC-to-ET mapping by an hour.
- Strategy timing windows (entry, flatten, close) are configured in UTC.
- A strategy configured with `entry_window_start_utc="13:35"` opens at 09:35 ET during DST, 08:35 ET during standard time. Either the strategy was tuned for DST and is wrong half the year, or vice versa.

**Consequence**:
- An intraday strategy may enter at a different relative-to-open time across the year.

**Remedy**:
- Phase B+1: switch timing windows from UTC to exchange-local time with explicit timezone handling.

**Mitigation today**:
- Audit each intraday strategy's timing across a DST boundary.

---

### L8 — Asset-class coverage is currently equity + FX only (INFORMATIONAL)

**Severity**: INFORMATIONAL — not a flaw, just a current scope.

**What's true**:
- TradePro currently sources data for cash equities, ETFs, and FX spot.
- No infrastructure exists for options chains, futures, or crypto.
- The cache layer (when built) needs to be asset-class-aware because schemas diverge sharply (options chains: snapshot per expiry × strike; futures: contract-rolling; crypto: 24/7 with multi-venue).

**Consequence**:
- Strategies that need options or futures data cannot be built today.
- This is not a limitation of what exists, but a constraint on what can be added.

**Remedy**:
- Phase B is designed asset-class-pluggable from day 1. Adding a new asset class will be a single-file plugin, not a schema rewrite.

**Mitigation today**: N/A.

---

## What the UI shows

**Settings → Data Health & Provider Preferences** (introduced in Phase A):

1. **Limitations panel** (this doc, rendered as accordion):
   - One row per limitation, severity colour-coded.
   - Click row → detail with consequence + remedy + mitigation.
   - Stale-review badge: limitation last reviewed > 30 days ago = highlight for re-review.

2. **Data assumptions registry**:
   - Each assumption gets a row: ID, description, current status (HONEST / PARTIAL / OPTIMISTIC / FICTIONAL), severity, remedy.
   - Editable so the assumption list grows with reality.

3. **Provider preferences** (editable):
   - Per (asset_class × resolution), the provider chain order.
   - Defaults: equity 1d → yfinance, equity 1m → yfinance (limit acknowledged), FX 1h → yfinance.
   - As new providers are added, they appear here.

4. **Backfill button** (Phase A: placeholder; Phase C: functional):
   - Per symbol × resolution × range.
   - Clear "Phase C, not yet implemented" badge for now.

---

## What this means for the project north-star

The north-star is:

> BUY/SELL/HOLD recommendation across ETF universe, evidenced by
> backtest + stress-scenario impact.

Translation through this doc:

- **For daily ETF BUY/SELL/HOLD from `ichimoku_equity` or daily scorers**: north-star is satisfied at a defensible quality level. Backtest evidence is real, stress scenarios runnable.
- **For intraday execution of those signals via `intraday_flat`**: north-star is **partially blocked** until Phase B/C/E ship. The strategy can run live and accumulate forward-test evidence; historical evidence is not currently producible.
- **For the system as a whole**: trustworthy backtest output requires the data layer roadmap below to ship at least through Phase D (reproducibility hashing). Phases B + C + E together unlock honest intraday backtesting.

---

## The shortest possible summary

If a future investor or trader asks "what's TradePro's track record":

- For daily strategies: "Backtested on 10+ years of daily Yahoo data; numbers within ~1–2% of reality due to slippage and data-revision drift."
- For intraday strategies: "No historical track record. Strategy logic is unit + BDD verified. Live forward testing began [date]."
- For the LLM gate's contribution: "Currently unmeasured. Will be measurable once the data layer's replay capability ships."

That's the honest answer. This doc exists so it stays the answer
until the remedy ships.
