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
| `index_us` | US context series (^VIX, ^TNX) | zero-volume bars are legitimate here |
| `index_uk` | UK context series (^FTSE, ^FTMC) | LSE calendar — awaiting seed, see below |

Retired to `~/.tradepro/bar_cache_quarantine/` (reversible, NOT deleted):
the whole `us_equity` tree (proven 0 unique partitions after CAVA's IPO
month was migrated), 22 non-LSE foreign listings, 4 HK, 12 futures,
4 indices, 9 crypto. **`us_etf` now contains zero non-US symbols.**
Audit end-state: clean except 30 known relic bars in SWDA.L 2010.

### S3 is now the SOURCE, local disk is a cache (22 Aug 2026, owner ruling)
Read-through is **ON**. A local miss downloads from
`s3://tradepro-bar-cache-108703420282/bar_cache/` and re-caches; the harvest
write-throughs on every partition write. The Mac is no longer the single
point of truth for market data. Verified by deleting a local partition and
watching the store restore it.

Config lives in `~/.tradepro/credentials` (`bar-cache-s3-bucket`), NOT in
plists — so every lane picks it up with no per-plist edit. Credentials fall
back to the scoped `bar-mirror` keys (read+write, deliberately no delete)
when boto3's default chain is empty, because the Mac's SSO session expires
and daemons would otherwise lose S3 silently.

- `TRADEPRO_BAR_CACHE_S3_DISABLE=1` forces local-only — **unit tests must set
  it**, or the credentials fallback turns offline tests into network tests.
- boto3 is now a CORE dependency. It was an optional extra, so the S3 path
  and the Secrets Manager path had both been failing silently for months.
- The nightly mirror lane still runs — write-through can miss if S3 blips,
  and the sync is the reconciliation sweep + staleness reporter.

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

0. ⚠️ **THE BAR-CACHE UNIVERSE CHANGED ON 2026-08-22.**
   `ls ~/.tradepro/bar_cache/us_etf` went **286 → 250** symbols (futures,
   indices, crypto, foreign listings removed; LSE ETFs moved to `uk_equity`;
   the `us_equity` tree retired). Any study or screen that derives its
   universe from that directory produces **results that are not comparable
   across the boundary** — say so explicitly in any gates doc that spans it.
   The tree IS the universe, which is also why the fix was to move the junk
   out of the tree rather than filter it at the screen.


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

## Owner action needed (needs ADMIN creds — the mirror key cannot do it)

**S3 stale-prefix cleanup.** The mirror IAM user (`tradepro-bar-mirror`) is
deliberately WRITE-ONLY — no `s3:DeleteObject` — so a compromised Mac cannot
wipe the backup. Good posture, but it means today's reorganisation left the
OLD layout live in the bucket alongside the new one. The retired data is
already COPIED to `retired_2026-08-22/` (31,174 objects), so this is a
tidy-up, not a rescue. With an admin profile:

    aws s3 rm s3://tradepro-bar-cache-108703420282/bar_cache/us_equity/ --recursive
    # then the 43 stale symbol prefixes under bar_cache/us_etf/ — the mirror
    # lane now LOGS the exact list each night ("STALE IN S3: …")

Until then a disaster-recovery restore would resurrect the retired trees.
Not urgent (nothing reads S3 — read-through was never enabled), but it means
the bucket is not yet a faithful mirror of canonical.

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
- 2026-08-23 (DATA): **SITE WAS DOWN 07:16–13:25 UTC. Read this before trusting
  anything dated 23 Aug.** db migration 065 (the IBKR x100 volume fix) rewrites
  1.6M rows and blew Dapper's 30s default command timeout, so it rolled back on
  every startup and the API refused to boot for six hours. Consequences for the
  research lane: (1) `ibkr_price_bars` volumes changed at **13:25:47 UTC**, NOT
  at the 4064d5c commit — the parquet store changed at commit time, so the two
  stores disagreed on volume units in between; anything computed off Postgres
  volumes this morning is in old units. (2) Any API-dependent job that ran in
  that window got an error page, not data — the refresh log shows it parsed the
  50x page. Re-run anything from that window. Fixed in b7f2183 (900s migration
  timeout + nginx no longer disguises a dead backend as a 401 password prompt).
  Also fixed: the worker heartbeat had not run since 17 Aug (uv resolved by
  guessing a path that does not exist here), aadc714.
  OPEN DEFECT, nobody owns it yet: `aws-redeploy` reported SUCCESS throughout
  the outage — a green deploy is not evidence the API is alive.
- 2026-08-22 (DATA): file created; data-platform truth + scoreboard as
  relayed by owner from RESEARCH session output.
- 2026-08-22 night (DATA): legacy cache.py retirement is now blocked only on
  DATA AVAILABILITY, not architecture. tradepro-refresh writes to the canonical
  store (resolver-routed) and no longer counts a legacy-cache SERVE as a
  refresh — it printed "10/10 refreshed" while writing nothing. UK symbols
  (BARC.L, ^FTSE …) cannot seed until Yahoo's throttle on this Mac clears;
  IBKR has no LSE entitlement. RE-RUN `tradepro-refresh --watchlist uk`
  when it does, then cache.py has only 2 read-only consumers left
  (wheel_backtest_run, straddle_scan) + the ibkr_bars fallback.
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

---

## 2026-08-22 (late) — RESEARCH lane

**⚠️ FOUR SYMBOLS STILL WRONG-CONTRACT ON DISK.** The wrong-venue purge covered
STX (now clean). It did NOT cover **MTUM (31), QUAL (34), USMV (26), VLUE (15)**
— counts are phantom bars, an unchanged close on ZERO volume. Evidence:
MTUM sat at 5,730–6,000 on volume 9–309 through June 2026 then printed 328.10
on volume 10,744 the next session; VLUE sat flat at exactly 1,861.00 on volume
zero for consecutive days in Feb 2023. Both `src=ibkr_web`. A write-time guard
does not help partitions already on disk.

**THE QUALITY TEST WAS WRONG IN BOTH DIRECTIONS.** Max-vs-recent-median ratio
falsely condemned **BILL** (genuinely fell $256.90 → $40 on 1.3M shares) and
**VIXY** (decay is what a VIX futures ETF does), while any threshold loose
enough to spare them would have cleared MTUM. A second attempt — "far from the
price level AND thin volume" — condemned **MU** for having risen 10x, because
old bars are legitimately cheaper and quieter. What separates them cleanly is
the zero-volume-unchanged-close count (MTUM 31 / QUAL 34 / USMV 26 / VLUE 15
vs STX 1, AMD 1, everything else 0). One implementation now in
`strategies/tradepro_strategies/universe.py`; the three near-copies are gone.

**THE UNIVERSE IS NOW DEFINED, NOT INFERRED.** `strategies/universe/tradeable.json`
— 266 scanned → **89 included**. Criteria: price ≥ $5, median turnover ≥
$10M/day, ≥ 500 sessions, ≤ 4 phantom bars, ≥ 90% recent coverage. Encodes the
owner's "solid stocks, not penny stocks" as numbers. **No screen lists a
directory any more, and there is deliberately no fallback if the file is
missing** — that fallback was the bug. Every exclusion carries a reason
(`universe.exclusion_reason("HPQ")` → "$2.5M/day, below the floor"). Beta and
volatility tiers ship with it for suite runs; beta is tiered on the 1000-day
window because over 252 days XLP correlates −0.04 with SPY (semis are driving
the index) while IVV correlates 0.97 — a regime, not a property.

**PROTOCOL BREACH RECORDED.** MR v1 FAILED G4 (top-1% tail share 26% vs ≤25%)
and the Swing screen shipped anyway with no reasoning written down, while
momentum v3 / analog v1 / intraday dip v1 were all held to "passes every gate".
Now recorded in `MEAN_REVERSION_GATES_V1.md` and flagged to the owner as an
open decision. Its G5 figure (−12.5%) is also wrong — measured pre-`_tradeable()`,
true value near −22%.

**TWO STUDIES REJECTED, ONE PARKED.**
- *Momentum v3* (entry volume) REJECTED — the edge inverts pre-2020.
- *Intraday dip v1* (the owner's own idea) REJECTED — 66% win, **−0.41%/trade**;
  a −8% stop against a +0.5% target needs 94% to break even.
- *Analog evaluation v1* PARKED before running — wrong priority.

**BIGGEST DATA GAP FOR RESEARCH: intraday coverage.** Median **14 sessions** of
5m bars across the 89 names, **zero symbols with a year**. The owner's
in-and-out strategy is untestable until that changes. This is now the single
highest-value data request from this lane.

**Screens fixed today:** settled-bar off-by-one (`>=` → `>`) was publishing
yesterday's close on both screens; published evidence on both was measured
pre-`_tradeable()` and understated the worst trade by roughly half.

**UPDATE, same day — both items closed by the DATA lane, verified by RESEARCH.**
18 poisoned partitions across MTUM/QUAL/USMV/VLUE **and STX** re-sourced from
IBKR. Independently re-checked here: phantom count 0 for all five, zero
zero-volume bars, MTUM's range back to 131–345. **The universe's quality
exclusion class is now EMPTY** — those names are excluded on liquidity alone.

Why the original purge missed them: the flat-phantom detector required 5+
CONSECUTIVE zero-volume sessions, and these interleave with traded bars. The
data lane has adopted the total-count statistic plus a better one — **median
volume == 0 across a whole month**, which no traded US listing ever shows, and
which catches a wrong-contract block even when its prices move.

Deep intraday was **structurally unreachable**, not under-run: IBKR measures
`period` backward from now unless given a `startTime` anchor (never exposed by
the endpoint), and the provider declared max_history = 30 days for every
intraday resolution, so BarStore skipped it as out-of-range. Both fixed. Real
measured limits: 1m ≈ 6 months, 5m works at 12/24/36 months, 1h ≈ 2 years.

RESEARCH has requested **24 months of 5m** (not 12) across the 89 tradeable
names — every study this session has died on the time-split gate, and 12
months leaves ~125 sessions per half, too thin to conclude from. 15m/30m
declined: 5m aggregates up losslessly. 6 months of 1m requested next if there
is headroom, for one purpose — resolving whether the session low preceded the
session high, the ambiguity that forced the owner's dip strategy to be graded
pessimistically.

**The MR v1 re-run is deliberately HELD until the backfill lands**, so it is
measured once against a stable store. Three inputs moved at once (universe
definition, cleaned data, intraday depth); measuring twice would produce two
irreconcilable numbers, which is exactly what the 4-vs-8-bar hold discrepancy
already is.
