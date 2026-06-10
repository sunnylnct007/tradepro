# TradePro Sprint Tracker
_Updated: 2026-06-10. Read this first in every new session — takes < 2 min._

---

## Repos & branches
| Worktree | Branch | Purpose |
|---|---|---|
| `tradepro-laneB/` | `main` | AWS deploy (API + frontend Docker via GitHub Actions) |
| `tradepro/tradepro/` | `live-main` | Mac strategies (Python). Commits flow directly into `main`. |

**Deploy flow:** push to `tradepro-laneB/main` → CI builds + redeploys automatically.

**Mac daemon restart after every deploy:**
```
launchctl kickstart -k gui/$(id -u)/com.tradepro.intraday-engine
```

---

## 🤝 Two-stream coordination status (2026-06-10)

### Stream A — Data quality / bar cache (this stream)
Ships: IBKR-primary harvest, scorecard CLI, reload-from-UI, data-worker daemon.
Commit: `e0cf05f` ✅ merged to main.

### Stream B — IBKR paper execution (other dev)
Ships: `ichimoku_equity_ibkr` running via IBKR OPG/MOO orders on account DUP656969,
OMS order recording, position-seed from PAPER Gateway, intraday_flat whipsaw fix
(persist + OMS-seed `entries_today`).
Latest: `93407c3` ✅ on main.

### Interface contracts — both streams must agree on these:
| Contract | Stream A | Stream B | Status |
|---|---|---|---|
| `TRADEPRO_IBKR_PORT` default | 7497 (bar_cache harvest, data-worker) | 7500 (paper-equity-ibkr plist) | ⚠️ **MISALIGNED** — see note below |
| IBKR client_id | 18 (IBKRProvider) | 21 (equity-ibkr plist), 17 (paper default) | ✅ no collision |
| `api_base_url` credentials | http://16.60.201.137 (EC2) | same | ✅ fixed 2026-06-10 |
| bar_cache 1m universe | 14 US ETFs (intraday_flat universe) | ichimoku_equity uses yfinance 1d (no bar_cache dep) | ✅ no overlap |
| `bar_cache.asset_class_resolver` | owns the module | ibkr.py imports for market-hours gate | ✅ read-only dep |

**⚠️ Port note:** `paper-equity-ibkr` plist has `TRADEPRO_IBKR_PORT=7500`.
User confirmed TWS is on **7497**. If `ichimoku_equity_ibkr` is failing to connect,
update the installed plist: `TRADEPRO_IBKR_PORT=7497` in `~/Library/LaunchAgents/com.tradepro.paper-equity-ibkr.plist`.

### Fixes applied 2026-06-10
- `~/.tradepro/credentials` `api_base_url` was pointing at `tradepro.showsoldprice.com` (dead Firebase domain)
  → updated to `http://16.60.201.137`. This unblocks `paper-equity-ibkr` universe fetches.
- `com.tradepro.paper-equity-ibkr` installed plist now has `TRADEPRO_API_BASE_URL=http://16.60.201.137` (belt-and-suspenders).
- `tradepro-laneB/main` rebased on top of Stream B's latest 5 commits — no conflicts.

---

## ✅ Friday replay backtest — COMPLETED (2026-06-09)

**Finding:** `intraday_flat` correctly produced **0 fills on June 5** because daily Ichimoku
signals were flat for all candidates (AAPL, MSFT, NVDA, AMZN, GOOGL, META, TSLA, QQQ, AMD, NFLX, AVGO).
The scanner log showed `scanner-drop-no-signal` for every symbol — "price not above cloud,
or tenkan/kijun not stacked, or chikou behind".

**What this means for the £2K loss investigation:**
- The paper-engine OMS confirms 0 fills across ALL `intraday_flat` IG sessions in June 1–9.
- The £1,859.94 IG history loss was from a **prior active period** (before June) when Ichimoku WAS aligned.
- The current running strategy is correctly sitting flat — no false positives.
- The IBKR positions (APLD, BABA, EC, MRVL, SWDA, VWRL) are **long-term holds**, not intraday_flat trades.

**Bar cache is now fully operational:**
- 390 RTH bars for June 5 in cache; `bar_cache_get --from 2026-06-05 --to 2026-06-05` → 390/390 COMPLETE via cache_hit_range
- Three bugs fixed: partial-partition cache hit + `--to` date off-by-one + empty-write wipeout protection
- Daily harvest cron installed: `com.tradepro.bar-cache-harvest` runs Mon–Fri 21:15 UTC via launchd
- Historical backfill: run with TWS open (`--from 2025-07-01`) to get 1yr of 1m bars

**Next scenario-sim goal:** forward-sim seeded with LIVE IBKR positions (APLD, BABA, EC, MRVL, SWDA, VWRL)
— stress these ACTUAL positions, not the paper candidates. Gate: data platform dependency (see memory).

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

### Shipped ✅ (continued)
- **C-5:** 2026-06-08 — Catalyst badge on SymbolCard (⚡ ALIGN green / ⚠ DIVG amber) + heatmap dot + rich tooltip. Commit 6661df4.
- **C-6:** 2026-06-08 — Catalyst expiry chip on SessionDetail decisions tab. Days counter RED when ≤3d. Commit 403d4bb.
- **Data feed fix (C-5/C-6):** 2026-06-08 — `intraday_flat` now injects `catalyst_flag/occurs_on/kind/title/days_until` into log_decision. Commit 53cea08.

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
| Bar cache | ✅ ~1950 bars/symbol for Jun 2–9 (5 sessions, yfinance); awaiting TWS for 1yr backfill |
| Bar cache bugs | ✅ Fixed: partial hit + off-by-one + empty-write wipeout guard + max_history clipping |
| Bar cache harvest cron | ✅ `com.tradepro.bar-cache-harvest` launchd loaded — runs Mon–Fri 21:15 UTC |
| Data worker daemon | ✅ Running PID 88291 — polls EC2 every 15s for backfill/reload/validate jobs |
| Data source badge on scan grid | ✅ Live (ibkr=blue, ig=purple, yfinance=gray) |
| DATA ERR sentinel card | ✅ Live — red card when all providers fail |
| Migration 045 | ✅ Deployed — ibkr first in provider chains |
| Mac intraday-engine daemon | ⏸ Paused (`~/.tradepro/intraday-engine.pause` exists) |
| paper-equity-ibkr daemon | ⚠️ Loaded, StartInterval=15min; was timing out on universe fetch (fixed) |
| EC2 (16.60.201.137) | ⚠️ Appears unreachable as of 2026-06-10 07:24 — data-worker getting ConnectTimeout |
| C-4 Finnhub CLI | ✅ Built — API key is in paper-equity-ibkr plist (`TRADEPRO_FINNHUB_API_KEY`) |
| C-5 Catalyst badge | ✅ Shipped 2026-06-08 (commit 6661df4) |
| C-6 Catalyst expiry chip | ✅ Shipped 2026-06-08 (commit 403d4bb) |

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
- yfinance 1m bars: **last 7 calendar days only** — sufficient for recent sessions
- yfinance daily: unlimited — all backtests above use daily bars
- **IBKR bar cache** (daily harvest running): ~1950 bars/symbol for Jun 2–9 via yfinance
  - **Historical backfill** (requires TWS on port 7497):
    ```bash
    cd strategies && TRADEPRO_IBKR_PORT=7497 \
    .venv/bin/tradepro-bar-cache-harvest \
      --from 2025-07-01 --to 2026-06-09 \
      --resolution 1m --asset us_etf --allow-partial --verbose
    ```
  - ~1 year × 12 symbols = ~168 month-partitions; IBKR does 30-day chunks → ~10 min

---

## TWS setup (one-time — unblocks Friday replay)

`ib_insync 0.9.86` is installed. **TWS is already connected** on port **7497** (live account U25124456).

### One-time setup (already done — for reference)

1. Open **IBKR Trader Workstation** (Classic TWS, NOT IBKR Desktop)
2. Menu: **Edit → Global Configuration → API → Settings**
3. Tick: ☑ **Enable ActiveX and Socket Clients**
4. Port: **7497** (confirmed working)
5. Trusted IPs: blank (localhost always allowed)
6. Click **OK / Apply**

### Verify TWS is still connected
```bash
cd /Users/skumar/sourcecode/tradepro/tradepro/strategies
TRADEPRO_IBKR_PORT=7497 .venv/bin/tradepro-verify-tws --date 2026-06-05
# Expected: ✓ SUCCESS — NNN bars  provider_used=ibkr
```

### Fetch any date into cache
```bash
TRADEPRO_IBKR_PORT=7497 .venv/bin/tradepro-bar-cache-get \
    --canonical SPY --asset us_etf --resolution 1m \
    --from 2026-06-05 --to 2026-06-05 -v
# → 390/390 COMPLETE via cache_hit_range (instant, no network call)
```

---

## To start a new session
1. Read this file
2. `git log --oneline -5` in `tradepro-laneB/`
3. Check daemon: `launchctl list | grep tradepro`
4. **Next:** Enable TWS API → run `tradepro-verify-tws` → Friday replay
