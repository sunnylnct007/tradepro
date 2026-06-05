# TradePro ⇄ Data Framework — Consumption Contract

**Status:** draft v1 (2026-06-05). **Purpose:** TradePro (this repo) and the data framework (separate repo, same owner, different session) are built in parallel. This is the **interface TradePro consumes** — the schema, freshness, and *correctness guarantees* the data framework must provide so integration is verifiable at the seam instead of discovered as a production bug. It is written from TradePro's existing consumption seams; the framework side should build to it, and TradePro will verify against it before switching off the yfinance stopgap.

> Why this exists: the data-correctness incidents of 2026-06-03..05 (LUK marked to $0, survivorship-flattered backtests, mark-source mismatches) were all *seam* failures — TradePro assumed something the data layer didn't guarantee. A contract makes those assumptions explicit and testable.

---

## 1. Daily bars (OHLCV) — `bar_cache` consumer

| Field | Type | Notes |
|---|---|---|
| `symbol` | string | Canonical (Yahoo-style) ticker. Must round-trip with the broker-ticker map. |
| `date` | date (UTC) | One row per trading day. |
| `open/high/low/close` | decimal | **Split- & dividend-adjusted** AND a raw `close` for execution modelling. |
| `volume` | int | |
| `currency` | string | |

**Guarantees required:**
- **No silent gaps.** A missing day is returned as an explicit "no data" marker, never omitted (omission reads as a holiday and corrupts indicators). Mirrors `regimes.py:184` "bars=0 + NaN so downstream shows 'no data'".
- **Delisting-safe.** A delisted/renamed symbol (the **LUK** lesson) must be flagged `status=delisted` with its last valid bar — never returned as $0 or 404-silently. Consumers must be able to carry such a holding at last-known price, not mark it to zero.
- **Freshness:** prior trading day's bar available by **07:00 local** (before the universe refresh + strategy runs).
- **Corporate actions** (splits/dividends/symbol changes) applied consistently and dated.

## 2. Point-in-time universe membership (survivorship)

The single most important backtest guarantee. The framework must answer: **"which symbols were in universe X *as of date D*?"** — not just today's members.
- Without this, every backtest silently deletes delisted/dropped names and **overstates returns**. This is non-negotiable for the backtest + scenario-sim roadmap.
- Schema: `(universe, symbol, valid_from, valid_to)` so membership is reconstructable for any historical date.

## 3. Intraday bars

Same shape as §1 at 1-min/5-min granularity, for `intraday_flat`/ORB. Must mark gaps explicitly (`bar_bus.py` consumes this; current yfinance path logs "NO BARS after retries" — the framework must instead return an explicit empty-with-reason).

## 4. Fundamentals — `core_portfolio/symbol_analysis_card` consumer

Consumes "the other dev's fundamental_analysis output" (symbol_analysis_card.py:134). Required per symbol: EPS (+ revision history — see `project_eps_analyst_coverage_gap`), revenue, margins, analyst coverage/rating, valuation multiples. **Freshness:** nightly. **Point-in-time:** as-reported, not restated, with report dates.

## 5. Quality gate — `core_portfolio/entry_timing` consumer

Consumes a **multi-year quality grade** (entry_timing.py:141,198). Contract: a per-symbol grade (e.g. A–F) + the inputs behind it (trend persistence, fundamental stability), refreshed on the fundamentals cadence, with the grade definition documented so TradePro's gate logic stays in sync.

## 6. Order-book volume (crypto)

`bar_cache/asset_classes/crypto.py` expects real volume from order books. Schema keeps volume in the bar; order-book depth optional/future.

---

## Delivery & verification

- **Interface:** TBD with the framework session — prefer a versioned read API (or a shared Postgres/columnar store) with an explicit schema version, so a breaking change is a version bump, not a silent shape change.
- **Verification (TradePro side):** before retiring the yfinance stopgap (`yf_noise.py`, `project_data_platform_dependency`), TradePro adds contract tests asserting each guarantee above (no-gaps, delisting-safe, point-in-time membership, freshness). These are the same class as the Phase 2 reconciliation invariants — correctness must be *provable*, not assumed.
- **Migration:** consume behind the existing seams (`bar_cache`, fundamentals, quality-gate) so flipping the source is a config change, not a rewrite (`project_data_platform_dependency`).

## Open items for the framework session
1. Confirm the delivery interface (API vs shared store) + schema versioning.
2. Confirm point-in-time universe membership is in scope (gates backtests + scenario sim).
3. Confirm delisting/corporate-action handling (the LUK class).
4. Agree the freshness SLAs (daily by 07:00; fundamentals nightly).
