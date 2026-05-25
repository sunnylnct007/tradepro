# Quant Engine — Known Gaps

Last updated: 2026-05-24

These gaps are ordered by severity. Each has a status tag:
- `CRITICAL` — blocks production use
- `MEDIUM` — reduces signal quality or operational efficiency
- `LOW` — nice-to-have / roadmap

---

## Gap 1 — Data Caching (CRITICAL)

**Problem:** The trader's reference DataFetcher hits Yahoo Finance on every run.
900+ tickers = 900 API calls, ~20 minutes, rate-limit risk. On a busy network
this will fail silently (yfinance returns empty DataFrames without raising).

**Context:** TradePro already has `tradepro_strategies/cache.py` with Parquet
storage (`ensure_cached()` / `load_cached()`). The quant engine library code is
deliberately pure (no fetching) so it can run in tests with synthetic data.

**Fix:** Before passing data into `Sleeve` or `Ensemble`, callers must call
`ensure_cached(ticker)` from `cache.py`. A convenience wrapper
`quant_engine.data_loader.load_sleeve_data(tickers, start, end)` should be
written that calls `ensure_cached` for each ticker and returns the
`dict[str, pd.DataFrame]` the sleeve expects.

**Status:** Library code is pure (no fetching). Integration with cache needed
before production use.

---

## Gap 2 — High-Beta Universe Builder (MEDIUM)

**Problem:** `UniverseBuilder.build_high_beta()` (not yet written) will need to
compute 252-day beta vs SPY for all S&P 500 + 400 tickers on every run
(900+ regression computations). With no persistence between runs, this becomes
a 3-hour job and a daily blocker.

**Fix:** Cache beta scores to a Parquet file at
`~/.tradepro/cache/beta_scores.parquet` with a weekly TTL. Re-compute only the
tickers whose cached score is older than 7 days. Expected runtime drops from
3 hours to < 5 minutes on subsequent runs.

**Status:** Not yet implemented. High-beta sleeve currently requires manual
ticker list in `QuantEngineConfig.large_50`.

---

## Gap 3 — FinBERT AI Signal Veto (Phase 2) (MEDIUM)

**Problem:** The spec requires: if S_t=1 AND FinBERT sentiment < -0.4 → veto
the long signal; if > +0.5 → apply 1.25x conviction multiplier. TradePro
already has `news_sentiment.py` (a sentiment pipeline), but the FinBERT bridge
is not wired into the quant engine signal pipeline.

**Required work:**
- `strategy/ai/finbert.py` — FinBERT score endpoint wrapper
- `data/news.py` — async headline fetcher (ticker → last 24h headlines)
- `quant_engine/sleeve.py` — accept optional `sentiment_gate` callable

**Status:** Roadmap Phase 2. The sleeve's `_compute_position_fn` injection
point makes this a clean add without touching existing logic.

---

## Gap 4 — Intraday FX Paper Trading (LOW)

**Problem:** The FX mean-reversion strategy (`fx_strategy.py`) backtests on
hourly data, but TradePro's paper engine only supports daily signals. There is
no hourly paper order management, no FX broker adapter, and no pip P&L
accounting (FX P&L is in pips, not % of equity).

**Fix:**
- Add `paper/brokers/fx_sim.py` — FX-specific fill simulator (spread model,
  pip accounting, leverage limits)
- Add hourly bar bus to `paper/bar_bus.py`
- Map `FXMeanReversionStrategy` position signals to `SignalRecord` objects

**Status:** Roadmap Phase 3. FX paper trading is a significant new subsystem.

---

## Gap 5 — Monte Carlo / Walk-Forward Visualisation (LOW)

**Problem:** Charts exist in `visualization.py` (Plotly). The quant engine
Monte Carlo fan chart and walk-forward OOS comparison are not exposed via MCP
or the React frontend.

**Fix:**
- Add `/api/quant/chart` endpoint (fan chart → base64 PNG or Plotly JSON)
- Add React component `<MonteCarloFanChart>` to the Backtest page

**Status:** Not yet implemented. The raw data is available via
`run_monte_carlo` MCP tool.

---

## Gap 6 — Sortino Not in Main Stats Class (LOW)

**Problem:** The trader's reference `main 3.py` computes Sortino. The existing
`Stats.summarise()` in `paper/` and `backtest.py` does not include it. There
is now a discrepancy between the quant engine metrics and the paper engine
metrics.

**Fix:** `quant_engine/portfolio_metrics.py` already has `sortino()`. The
`Stats` class in `paper/` and `backtest.py` should be updated to call it.
Blocked: those files are owned by a different developer stream.

**Status:** Fixed in `quant_engine/portfolio_metrics.summarise()`. Old
`Stats` class not updated (out of scope for this sprint).

---

## Gap 7 — Walk-Forward Window Ends at 2025 (Partial Year) (MEDIUM)

**Problem:** Config window 5 tests year 2025, which is incomplete as of
2026-05 (only ~5 months of data available). The `WalkForwardValidator` does
not detect this and will compute Sharpe/CAGR on a partial year, which inflates
or deflates the annualised metrics depending on market direction.

**Fix:** In `WalkForwardWindow`, add a `is_partial_year: bool` field. In
`WalkForwardValidator.run()`, compare `test_end` to today's date; if the test
window has not completed, set `is_partial_year=True` and include a warning in
the result. Callers should not normalise partial-year CAGR.

**Status:** Known issue. No guard in code yet.

---

## Gap 8 — No Retry / Rate Limiting on DataFetcher (CRITICAL when live)

**Problem:** Any `DataFetcher.fetch()` (when written) will silently return None
on failure. No exponential back-off, no queue. For 900 tickers in parallel,
Yahoo Finance will rate-limit or block after ~200 requests (~20 per second is
the empirical limit).

**Fix:**
- Add `tenacity` retry decorator with jittered exponential back-off
  (e.g. `wait_random_exponential(multiplier=1, max=60)`, `stop_after_attempt(5)`)
- Batch ticker fetches with a `asyncio.sleep(0.1)` between batches of 10
- Log the final None-returns count so operators can see the data quality

**Status:** Not yet implemented. Must be addressed before any live run on a
universe > 50 tickers.

---

## Gap 9 — Crypto Exclusion List Is Static (LOW)

**Problem:** The `crypto_exclude` frozenset in `QuantEngineConfig` hardcodes
~30 known crypto proxies. New names added to indices (e.g. MSTU, MSTZ — 2×
leveraged Bitcoin ETFs added to S&P indices in 2025) will slip through the
filter and pollute the equity sleeve with crypto-correlated beta.

**Fix:** Extend the exclusion logic with a dynamic SIC code / NAICS check:
if `yfinance.Ticker(t).info.get("sector") in {"Cryptocurrency", "Financial
Services"}` and the name contains "Bitcoin" / "Crypto" → exclude. Fallback
to the static list when yfinance is unreachable.

**Status:** Static list only. Manual update required when new crypto proxies
enter major indices.

---

## Gap 10 — No Live Position Bridge (LOW)

**Problem:** Backtest results exist only in memory. There is no bridge from
the quant engine to TradePro's `paper/` system to convert a high-scoring
sleeve weight into a paper order. A trader cannot act on a `SleeveResult`
today without writing custom glue code.

**Fix:** Add `quant_engine/bridge.py` with a `sleeve_to_signals(result:
SleeveResult, strategy_id: str) -> list[SignalRecord]` function that emits
`SignalRecord` objects into the paper ledger — the same format `paper/engine.py`
consumes. This would allow running the quant engine daily and automatically
populating the paper engine's pending orders queue.

**Status:** Roadmap item. Not yet implemented.
