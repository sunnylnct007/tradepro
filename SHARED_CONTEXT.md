# SHARED_CONTEXT — cross-session handover board

**Purpose**: one file BOTH working agents (and the owner) read and update, so
neither session acts on a stale picture of the other's work. Repo-committed on
purpose: it travels with every checkout and survives context loss.

**Protocol**: append dated entries under your lane; never rewrite the other
lane's entries — correct them with a new dated line. Keep it under ~150 lines;
prune superseded entries when you update. No secrets (repo is PUBLIC).

**Lanes**:
- **DATA/PLATFORM session** — bar store, harvest lanes, IBKR connectivity,
  backend/API, desk infrastructure, deploys.
- **RESEARCH session** — strategy studies (pre-registered gates protocol),
  Research view, swing candidates, verdict logic.

---

## Current truth — 2026-08-22 (evening)

### Store layout — CANONICAL as of 2026-08-22 (read this before touching data)
Three trees under `~/.tradepro/bar_cache/`, each governed by
`resolve_asset_class()` (no caller hardcodes a tree any more):

| tree | holds | notes |
|---|---|---|
| `us_etf` | **250** US-listed ETFs + single names | the everything-bucket for US; `us_equity` folds into it |
| `uk_equity` | **19** LSE ETFs (`.L`) | relocated 22 Aug; LSE calendar, not NYSE |
| `index_us` | `^`-prefixed context series (^VIX, ^TNX) | zero-volume bars are legitimate here |

Retired to `~/.tradepro/bar_cache_quarantine/` (reversible, NOT deleted):
the whole `us_equity` tree (proven 0 unique partitions after CAVA's IPO
month was migrated), 22 non-LSE foreign listings, 4 HK, 12 futures,
4 indices, 9 crypto. **`us_etf` now contains zero non-US symbols.**
Audit end-state: clean except 30 known relic bars in SWDA.L 2010.

### Data platform (DATA lane)
- **The parquet store is certified clean.** `tradepro-bar-cache-audit` (new
  CLI, weekly Sat 10:00 lane, reports ok/warn to run log) sweeps every
  partition. Wrong-venue poison found + purged 22 Aug: VLUE/USMV/QUAL/MTUM/STX
  1d in BOTH us_etf and us_equity trees held a wrong contract's series
  (VLUE flat 2536.93 zero-vol; STX in LSE pence). All 64 poisoned partitions
  re-sourced from IBKR and verified. Residual: 30 flags in SWDA.L partitions
  from 2010 (foreign, unharvested, in no universe) — documented, untouched.
- **Write-time guards**: NaN, isolated-spike, and NEW flat-phantom check
  (5+ identical zero-volume SESSIONS rejects the frame; daily-spaced only).
  Rejected frames are QUARANTINED (~/.tradepro/quarantine/ + run-log warn),
  never silently dropped. Shrink guard now allows a validated force_refresh
  covering every expected session to replace a larger poisoned partition.
- **One canonical tree**: writes go to `us_etf` only (us_equity write fork
  retired 22 Aug; directory still readable, deletion pending triage of its
  26 unique symbols — crypto/HK/LSE misfiles). Legacy yahoo cache still
  serves ~10 consumers (retirement blocked on an index asset-class for
  ^VIX/^TNX and a populated uk_equity store) — live-portfolio and
  equity-pipeline already migrated to the golden chain.
- **S3 mirror is current** as of 22 Aug ~13:00 UTC (was 1,042 files behind;
  manually synced; nightly lane has its own IAM keys, not SSO).
- **IBKR request volume**: ~200k/day → low thousands. Conid cache (+ US-venue
  preference fix — BA was resolving to BAE/LSE, 16 names dark), option
  months/strikes/contract caches, batched screener snapshots, RTH-gated C#
  harvester, delta-fetch + merge writes, harvest circuit breaker, US-only
  universe (dot + -USD excluded).
- **Wheel screen inputs un-darked**: dividend yield via ibkr→finnhub→yahoo
  (per-row source); OI capture lane nightly 22:15 (Yahoo throttle on this Mac
  still cooling as of 22 Aug). Options screen ~2,900 → ~700 req/run expected.

### Strategy scoreboard (RESEARCH lane — as reported 22 Aug evening)
- Wheel v3: DO NOT FUND. QDB: killed. Intraday S/R: killed.
- ICH entry filters (3 constructions): dead end, proven — gates failed.
- ICH exit v1: variant C (min-hold 20) beats spec on win rate AND return;
  variant B (+41% return) fails owner's win-rate floor.
- **ICH exit v2 (profit targets): FAIL — ~44% win is the family's ceiling.**
- **Mean-reversion v1: PARTIAL — 62.4% win, 4-bar median hold, 5 of 6 gates.**
  Ran with an IN-BACKTEST data guard, not against the cleaned store.
- Swing screen: settled-bar defect fixed (reads last settled session; schedule
  22:00 + 12:00 catch-up instead of 20-min recompute theatre).

---

## Open handovers

1. **MR v1 confirmation re-run — UNBLOCKED as of 22 Aug afternoon.**
   Research session's handover item 3 waited on the data-lane validator +
   clean store; both landed (see Current truth). Action: re-run the
   mean-reversion study against the store WITHOUT the in-backtest guard and
   diff vs 62.4%/4-bar/5-of-6. RESEARCH lane owns the harness; DATA lane
   certifies the store it reads. If results shift materially, the in-backtest
   guard did not match reality — say so loudly in the gates doc.
2. **Concurrent-session hygiene**: both sessions commit to `live-main` →
   push to origin/main (CI/CD deploys). Commit with EXPLICIT paths only;
   `git pull --rebase` if push rejects. Frontend is high-collision territory
   (desk redesign + chart work same day).
3. **Data screen**: grading now provenance-based (dominant stored source);
   "missing days" judged against a symbol's own coverage. If a screen number
   looks insane again, suspect the METRIC's denominator before the data.

## Owner rulings in force (do not relitigate)
- **No horizon expansion.** IBKR leverage backlog (scanner API, /hmds,
  WebSocket, wider snapshot capture) is PARKED until the current stack proves
  itself. Spec v2: three products only.
- Win rate 34-35% is a no-go for the platform; MR family gate is ≥55%.
- Second IBKR data user: DEFERRED — collect market-hours `degraded` probe
  counts for a week first. Login discipline + pause button meanwhile.
- Verbatim-port rule: researched exit variants (B/D/C…) must never silently
  replace the live signal; adoption needs explicit owner decision + parity test.
- One source of truth: continue consolidation (waves logged in DATA lane's
  memory); no new stores, no local durable files.

## Update log
- 2026-08-22 (DATA): file created; data-platform truth + scoreboard as
  relayed by owner from RESEARCH session output.
- 2026-08-22 evening (DATA): ONE-SOURCE-OF-TRUTH consolidation essentially
  COMPLETE — store reduced to three properly-classed trees (see table above),
  us_equity retired after proving 0 unique partitions, resolver now governs
  all routing, paper bus reads the canonical store (signal and fills finally
  agree — they did not before), index_us shipped so ^VIX/^TNX are golden.
  Legacy cache.py is down to ONE blocker: refresh.py UK watchlist wants
  UK SINGLE NAMES (BARC.L, SHEL.L …) which the store does not hold — the 19
  it holds are ETFs. Everything else migrated.
- 2026-08-22 late (DATA): Data screen fully sorted + deployed — one row per
  symbol (us_equity display twins retired; React key collision fixed),
  provenance-true provider column (all 243 ibkr_web/ok), chart stack (volume,
  RVOL ×avg readout, SMA lead-in 310d, VWAP, RSI pane, ⛶ maximize), 5m/15m/
  30m/1h DERIVED from 1m server-side (never stored). New symbols seeded gold:
  SNDK, RKLB, ARM, GFS, SKHY (SK Hynix ADR — owner was right, listing is
  real; verified vs Finnhub to the cent). HXSCL dead. Legacy-cache Wave 1
  COMPLETE: MCP analysis tools ×4, run_backtest, build_high_beta, worker now
  golden-first via ibkr_bars.golden_daily (ensure_cached-compatible).
  Hygiene note for either lane: harvest health POSTs are fire-and-forget —
  3 of 5 new-symbol records dropped silently once; deserves retry-once+warn.
