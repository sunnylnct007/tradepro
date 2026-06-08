# TradePro Sprint Tracker
_Updated: 2026-06-08. Read this first in every new session — takes < 2 min._

---

## Repos & branches
| Worktree | Branch | Purpose |
|---|---|---|
| `tradepro-laneB/` | `main` | AWS deploy (API + frontend Docker via GitHub Actions) |
| `tradepro/tradepro/` | `desk-fixes-2` | Mac strategies (Python). Cherry-pick to main to deploy. |

**Deploy flow:** commit to `desk-fixes-2` → cherry-pick to `tradepro-laneB/main` → `git push` → CI builds + redeploys automatically.

**Mac daemon restart after every deploy:**
```
launchctl kickstart -k gui/$(id -u)/com.tradepro.intraday-engine
```

---

## 🔴 CURRENT PRIORITY — Friday replay backtest

**User goal:** understand the ~£2K loss on Friday (2026-06-06). Simulate what happened, see worst-case exposure, and use this as the template for "stress this book" forward sim.

**Context:**
- The loss came from `intraday_flat` EQUITY strategy (294 trades over 7 days → −£1,859.94 verified from IG history)
- The whole point of bar/orderbook harvesting (IBKR + bar_cache) is to enable this simulation
- Need: replay Friday's session with the live positions as seed, surface P&L attribution per symbol + per strategy

### What exists already
- `tradepro-paper-backtest` CLI — runs historical paper sessions
- `tradepro-quant-backtest` CLI — quant walk-forward backtest
- `tradepro-equity-pipeline` CLI — equity data pipeline with preflight
- Bar cache is **empty** (IBKR TWS not connected yet) — Friday replay would use yfinance 7-day window which covers 2026-06-06 ✅
- `oms_orders` table has Friday's actual orders (broker golden source)
- Key files:
  - `strategies/tradepro_strategies/cli/` — all CLI entry points
  - `strategies/features/` — BDD specs
  - `backend/TradePro.Api/Endpoints/` — backtest trigger endpoints

### What's needed for Friday replay
1. **Replay session seeded with live positions** — `paper_session --broker yfinance --date 2026-06-06 --seed-positions-from-broker`
2. **P&L attribution** — per symbol, per strategy, actual vs simulated
3. **Worst-case exposure report** — max drawdown during the session, biggest losers
4. **UI surface** — show the replay result on the cockpit (scenario sim panel)

### Key question to answer first
Run this to see what Friday's OMS orders look like:
```
curl http://16.60.201.137/api/admin/oms-orders?date=2026-06-06
```
Or via MCP: `mcp__tradepro__list_orders`

---

## Stream 1 — Catalyst Overlay
### Shipped ✅
- C-1: `catalysts` DB table + CRUD endpoints + cockpit CatalystsSection
- C-2: `catalysts_sink` + extractor daemon (Yahoo news → keyword extractor)
- C-3.1/3.2/3.3: LLM gate enricher + CatalystFetcher + StrategyRunner auto-wiring
- C-4 (2026-06-07): **Finnhub news + earnings calendar pipeline**
  - `catalysts_finnhub.py` — fetches /company-news + /calendar/earnings
  - `cli/catalysts_finnhub_sweep.py` — full-universe sweep CLI
  - `scripts/com.tradepro.catalyst-finnhub.plist` — launchd every 2h
  - **ACTION NEEDED:** Copy plist to `~/Library/LaunchAgents/` and fill in `TRADEPRO_FINNHUB_API_KEY`

### Remaining ❌
- **C-5:** Catalyst badge on scan-grid `SymbolCard` — show ⚡ pill when `detail.catalyst_flag === "tech_event_alignment"` (green) or ⚠ when `"tech_event_divergence"` (amber). File: `frontend/src/components/cockpit/SymbolScanGrid.tsx`
- **C-6:** Catalyst expiry chip on session detail page

---

## Stream 2 — 3-Axis Signal Schema (horizon · strategy_type · conviction)
- Spec exists in `SIGNAL_CARD_SPEC_v1.md` + `IMPROVEMENT_SUGGESTIONS_v1.md §1.3`
- `conviction` already implemented in `compare.py` (BUG-001/002 fixed)
- **Gap:** `horizon` + `strategy_type` not yet in the signal payload to frontend
- **Gap:** Frontend card doesn't show these axes

---

## Stream 3 — IBKR Cockpit Re-skin
- **NOT STARTED** — gate: canonical P&L solid ✅, start when backtest priority clears
- KPI strip + tabbed work-area + sparklines + right-rail chart+news

---

## Stream 4 — Phase 2 Portfolio-Aware Engine
- **PARKED** — too early, wait until backtest trust is built

---

## Infrastructure state
| Thing | Status |
|---|---|
| Bar cache | ⚡ `ib_insync 0.9.86` installed; **TWS API not enabled yet** — see TWS setup section below |
| Data source badge on scan grid | ✅ Live (ibkr=blue, ig=purple, yfinance=gray) |
| DATA ERR sentinel card | ✅ Live — red card when all providers fail |
| Migration 045 | ✅ Deployed — ibkr first in provider chains |
| Mac intraday-engine daemon | ✅ PID 24063 |
| C-4 Finnhub CLI | ✅ Built — needs API key in plist |

---

## Backtest commands (run from tradepro/strategies/ with .venv active)

### ICH Equity (5 large-caps, 2yr)
```bash
.venv/bin/tradepro-quant-backtest --legacy-cache --payload '{
  "strategy": "ichimoku_equity",
  "symbols": ["AAPL","MSFT","NVDA","AMZN","GOOGL"],
  "start": "2024-01-01", "end": "2026-06-05",
  "initial_capital": 100000, "benchmark": "SPY",
  "monte_carlo": {"n_sims": 200, "years": 3, "seed": 42}
}'
# Result: CAGR 18.5% | Sharpe 1.39 | Max DD -10.6% | beats SPY on risk-adj
```

### ICH FX Mean-Reversion (5 pairs, 2yr)
```bash
.venv/bin/tradepro-quant-backtest --legacy-cache --payload '{
  "strategy": "ichimoku_fx_mr",
  "symbols": ["EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X"],
  "start": "2024-01-01", "end": "2026-06-05",
  "initial_capital": 100000, "benchmark": "SPY",
  "monte_carlo": {"n_sims": 200, "years": 3, "seed": 42}
}'
# Result: CAGR 2.1% | Sharpe 0.53 | Max DD -3.1% | low edge on FX
```

### Friday fill attribution (when EC2 up)
```bash
.venv/bin/tradepro-fill-attribution --date 2026-06-06
```

### Simulation replay (yfinance, last 5 trading days only)
```bash
.venv/bin/tradepro-replay-session --date 2026-06-05 --strategy intraday_flat
```

## Key data constraint
- yfinance 1m bars: **last 7 calendar days only** — can't replay last Friday
- yfinance daily: unlimited — all backtests above use daily bars
- **IBKR bar cache** (empty until TWS connects): unlocks unlimited 1m history
  → connect TWS + trigger harvest in Settings › Data Health → Friday replay works

---

## TWS setup (one-time — unblocks Friday replay)

`ib_insync 0.9.86` is installed. The bar provider connects to TWS at localhost:7497.

### Steps to enable

1. Open **IBKR Trader Workstation** (the desktop app)
2. Menu: **Edit → Global Configuration → API → Settings**
3. Tick: ☑ **Enable ActiveX and Socket Clients**
4. Port: **7497** (TWS paper default — leave as is)
5. Trusted IPs: add `127.0.0.1` or leave blank (localhost is always allowed)
6. Uncheck **Read-Only API** is optional — we connect `readonly=True` anyway
7. Click **OK / Apply** — TWS restarts the socket listener immediately

### Verify
```bash
cd /Users/skumar/sourcecode/tradepro/tradepro/strategies
.venv/bin/tradepro-verify-tws --date 2026-06-05
# Expected: ✓ SUCCESS — NNN bars  provider_used=ibkr
```

### Then trigger Friday replay
```bash
# Once ✓ SUCCESS above, fetch the bars into cache:
.venv/bin/tradepro-bar-cache-get \
    --canonical SPY --asset us_etf --resolution 1m \
    --from 2026-06-06 --to 2026-06-06 -v

# Then replay that session with the real data:
.venv/bin/tradepro-replay-session --date 2026-06-06 --strategy intraday_flat
```

---

## To start a new session
1. Read this file
2. `git log --oneline -5` in `tradepro-laneB/`
3. Check daemon: `launchctl list | grep tradepro`
4. **Next:** Enable TWS API → run `tradepro-verify-tws` → Friday replay
