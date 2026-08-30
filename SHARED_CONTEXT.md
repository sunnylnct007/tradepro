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
- 2026-08-23 evening (DATA): **cache.py retirement — the stated blocker was WRONG,
  and the real one is worse.**
  - "UK symbols cannot seed until Yahoo's throttle on this Mac clears" (22 Aug
    entry below) was never going to happen. The 429s were **self-inflicted**: our
    bar provider handed yfinance a bare `curl_cffi` session built for timeout
    safety, which replaced yfinance's browser-impersonating default, and Yahoo's
    bot detection answers a non-browser fingerprint with "Too Many Requests".
    Measured back-to-back in one process: no session → 5 rows; `Session(timeout=8)`
    → YFRateLimitError; `Session(timeout=8, impersonate="chrome")` → 5 rows.
    Fixed in 7fe1039. **Yahoo is available again for seeding — plan accordingly.**
  - Seeded as proof: `^VIX` 4185/4185 bars COMPLETE and `^TNX` 4182, both
    2010-01-04 → 2026-08-21, matching the legacy cache row-for-row. These were
    the two symbols falling back to legacy this morning.
  - **The REAL blocker: the canonical store has no adjusted-close series, and its
    `close` column silently mixes two conventions.** `adj_factor` is 1.0 for all
    271 symbols — it carries no corporate-action information. Worse, measured on
    SPY against the legacy cache: rows sourced from **yfinance are dividend-
    ADJUSTED** (median 0.26% from legacy adj_close, 14.4% from raw), while rows
    from **ibkr / ibkr_web are RAW** (0.00–0.09% from legacy raw close). Sources
    alternate by monthly partition, so one symbol's series changes convention
    partway through.
  - Size of the seam, i.e. raw-vs-adjusted gap by era (SPY): 2015 16.4% ·
    2018 11.2% · 2021 6.3% · 2023 3.4% · 2025 1.1% · 2026 0.26%. It shrinks
    toward the present, so recent short-hold signals are barely affected, but
    anything long-horizon crossing a seam is biased — SMA200, 52-week high/low,
    and any multi-year backtest. NOT a crisis for 4-bar Swing; DO check it before
    trusting a long-lookback result.
  - Consequence: `wheel_backtest_run` and `straddle_scan` read
    `load_cached("yahoo", …)` and prefer `adj_close`. Migrating them to the
    canonical store today would **silently swap adjusted prices for raw** —
    exactly the class of change DATA_CHANGE_LOG exists to prevent. Left on legacy
    on purpose. `market_context.py`'s import was dead and is removed (this commit);
    `compare.py` and `ibkr_bars.py` use legacy only as a visible fallback.
  - **To actually finish the retirement, someone must first decide the store's
    close convention and populate `adj_factor` for real.** That is the task; the
    Yahoo throttle never was.
  - Unrelated, pre-existing (NOT caused by this work): 3 failures in
    `tests/test_equity_risk_controls.py` (settled-bar / partial-bar cross), a
    pandas ValueError. Present on a clean tree too.
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


---

## 2026-08-24 — the IBKR MCP connector TAKES THE MARKET-DATA SESSION

Recorded because it was misdiagnosed once already today.

The health probe reported `degraded — auth VALID but snapshot DARK (SPY served
no last/IV after warm-up retry) — market-data session contention`. It was
attributed to the owner being logged into the IBKR portal. He was **not**
logged in.

The actual cause was almost certainly the **IBKR MCP connector**, used from
this session minutes earlier to look up SK Hynix and TSMC contract IDs and
pull a year of KRX price history. That connector authenticates against the
same account and takes the same single market-data session TradePro needs.

Confirmed by re-running the probe once nothing was holding it:
`ibkr-health: ok — auth + live snapshot`.

**Operational consequence.** The known contention list was "the owner's portal
login, or another client". It also includes **any Claude session calling the
IBKR MCP tools** — which is easy to do accidentally while investigating, and
which looks identical to a portal login from the probe's side.

**Practical rule:** treat an IBKR MCP call as taking the trading session for
its duration. During market hours, and especially during the forward-test
window, prefer the stored bar cache or the Web API (`/api/integrations/ibkr/*`)
over the MCP connector. The Web API kept serving account state throughout —
NLV, positions and live marks were all available while the snapshot was dark,
which is why Swing was unaffected.

**What was NOT affected:** Swing. Signals come from stored daily bars,
positions from the Web API, and market orders need no quote. Verified end to
end while the session was dark: position seed succeeded, session completed,
exit 0. The options desk WOULD have been affected — it needs IV, greeks and
open interest from exactly that session.

## 2026-08-26 — ibkr-gateway RETIRED (data/platform lane)

Owner's call: "we dont need ibkr-gateway as we have webapi working." Verified
and done. `com.tradepro.ibkr-gateway` is `bootout`ed and its plist moved to
`~/Library/LaunchAgents/retired/` (reversible).

Why it cost nothing:
- **Nothing was listening on port 7500.** TWS/IB Gateway (the desktop app it
  talks to) was not running. The daemon sat in a reconnect loop — 50,331
  refusal lines since 25 Aug alone.
- **Last order it ever placed: 6 July**, seven weeks ago. Outbox spans
  18 Jun → 6 Jul, 149 outcomes, inbox empty — nothing stranded.
- **Reads** were already 100% Web API: the swing log says `position seed: via
  IBKR WEB API (no gateway)` on every cycle.
- **Writes** go via the OMS confirmed path by default — `T212OrderRouter →
  POST /api/oms/orders → ApproveAsync → PlaceMarketOrderConfirmedAsync`
  (IBKRClient.cs:1014). Verified live today: the daemon logs "orders route via
  the OMS push path" and `/api/oms/orders` returns 200.

**Bonus — this closes a data leak.** All 7,037 bad historical closes are
`source == "ibkr"` (this socket provider); **zero** from `ibkr_web`. The
corrupting write path is now gone, so the repair in ADJ_FACTOR_MIGRATION_PLAN
§7 is cleanup of a fixed population rather than an ongoing leak.

**Still owed:** `bar_cache/providers/ibkr_provider.py` (the socket bar provider)
is still in the provider chain and can no longer connect. It should be dropped
from the chain rather than left to fail and fall through — but that changes
provider order, so it wants the store session, not the middle of a forward test.

**A correction worth carrying:** I first concluded there was no Web API
execution path, having grepped only Python. There is one, in .NET. If you are
tracing execution, look at `PostgresOmsService.cs` and `IBKRClient.cs` — the
Python side only *pushes* to the OMS.

## 2026-08-26 — WHEEL SCREEN: 0 eligible was OUR BUG, not missing data

The desk showed "none eligible" with 67 of 82 rows blocked on *"IV-Rank
unavailable — cannot confirm the vega edge"* — while the SAME ROWS displayed a
vega edge (NVDA 1.21, SLV 1.21, XLF 1.46, TLT 1.07). Both cannot be true.

Cause was ordering in `cli/options_screen.py`:

```
1384  ctx = MarketContext(iv_hv_ratio = ... if ivr.available else None)
1405  evaluate(cand, ctx, ...)                  <- GATE RAN HERE (saw None)
1426  iv_solved = solve_iv_and_crosscheck(...)  <- solve succeeded HERE
1436  ivr = replace(ivr, available=True, iv_hv_ratio=1.21)  -> the DISPLAY
```

The 15 Aug "solve IV, don't just fetch it" work was correct all along; it just
ran after its only consumer. Fixed in 998fee7 — solve moved above `ctx`, exactly
one solve site.

**Measured before/after on the same three symbols:** 0 eligible → **2 of 3**
(SLV best, CSP $57.5, 26.6% annualised; XLF 10%/yr). NVDA now blocks on a REAL
reason — notional £15,354 over the £10,000 per-position limit.

**No thresholds moved.** A thin bridge still blocks (AMZN 0.61 < 0.95 is a
genuine rejection) and truly absent vega data still blocks. IV-Rank remains
legitimately `n/a` — the accumulated window is 12d against a 60d minimum, so
the BRIDGE is carrying the gate exactly as designed. It was simply unreachable.

**For the research lane:** any wheel/options result computed before 998fee7 was
graded with the vega gate hard-blocked on ~80% of rows. Re-run anything that
depended on wheel eligibility.

**Method note worth keeping.** 752 tests passed throughout. The solve, the gate
and MarketContext were each correct in isolation; only their ORDER was wrong,
and nothing asserted a relationship between them. The guard added is therefore
two-part — semantic (a populated ratio never yields "unavailable") and
source-order — and the ordering guard was VERIFIED to fail against the pre-fix
file before being trusted.

---

## 30 Aug 2026 — THE WHEEL SCREEN IS REJECTING ON FABRICATED LIQUIDITY

**For whoever picks up the options/wheel lane. Diagnosis only — nothing fixed yet.**

An external review flagged the wheel board as untrustworthy. I verified every
claim against the live API and the harvest logs rather than accepting them.
Most were right; two were wrong in ways that change the priority order.

### CONFIRMED, and it is the headline

The chain source has fallen back to yfinance, and its open interest is
fiction. From the 28 Aug screen run, blocking with "illiquid, bad fills":

    SPY   OI 194 < 250        QQQ   OI  46 < 250
    DIA   OI  12 < 250        IBKR  OI   2 < 250
    ACN   OI  28 < 250        MS    OI  16 < 250

SPY options are among the deepest markets in existence. An external check on
XOM put the same figure at 57 from yfinance against 7,570 live on IBKR, with
3,783 on the bid, and the spread gate likewise false (21.5% claimed vs 12.7%
real). So the liquidity and spread gates have been firing on garbage for at
least 44 hours and plausibly since bars_1m went down on the 21st.

THIS IS THE ANSWER TO "the screen rejects everything and I cannot tell if the
reasoning is sound". It was not reasoning. FIX THIS FIRST — every other item
below is secondary to it.

The IV/HV block is the one legitimate rejection (XOM 0.899 vs the 0.95 gate),
but IV also comes off the same degraded chain, so re-measure it before
concluding anything. Note that with every board name currently under 0.95 a
hard gate means nothing is tradeable at all; a graded version is the better
shape, but that is a design call, not a bug fix.

### CORRECTION 1 — "options_screen broken since 28 Aug, 44h" is mostly a weekend

It last ran Fri 19:59Z and exited rc=0. Only ~4 of those 44 hours are weekday
time. The readiness check applies a weekday adjustment to bars_1m ("121h of
them weekday time") and NOT to options_screen — the same false-alarm class
already fixed for the health probe in 3f252df.

The 37 CONSECUTIVE DEGRADED RUNS are real and are a different fault: the screen
runs fine, it is the OUTPUT that is degraded. Do not chase a scheduling ghost.

### CORRECTION 2 — bars_1d is a FAIL-OPEN MONITOR, and the harvest is innocent

Readiness reports bars_1d as "all 1 symbols covered — 0 from IBKR, 1 from the
yfinance fallback" AND usable:true. The harvest is fine: 28 Aug ran 244 symbols,
244 GOLD, 0 partial, 0 failed.

What happened is that the swing refresh at 09:30-09:31Z on 29 Aug did an
incidental single-symbol cache-miss fetch, and THAT stamped the lane telemetry
at 09:32:57Z. Coverage is computed against the run's own symbol count, so 1-of-1
reads as 100% and the dataset reports healthy.

So ANY ad-hoc single-symbol fetch silently overwrites the health of the entire
daily lane. That is a fail-open monitor over the dataset feeding every HV,
regime, Ichimoku and backtest figure — and it is why this passed three runs
without an alarm. Same shape as the 5 monitors found green while broken on
17-18 Aug.

Do NOT "fix the daily harvest". It is not broken. Fix the telemetry rollup so a
partial fetch cannot masquerade as a full-universe run.

### RETRACTED — "the book is out of sync" WAS NOT A DEFECT

I wrote this up as a bug on 30 Aug. It is not one, and implementing the
"reconcile the book" item below would DAMAGE the paper record. Correcting it
here because it is the kind of plausible-sounding fix another agent would act
on.

What I claimed: the board reports 1 open position (SLV) while the account holds
ten short options (AMZN, APLD x2, GOOGL, IBM, MRVL x2, PG, SKHY, XOM), so the
size and concentration gates compute against the wrong book.

Why that is wrong, per the owner: THESE ARE TWO DIFFERENT ACCOUNTS AND ALWAYS
WERE. `options_paper_position` is the PAPER wheel ledger. The IBKR account is
the LIVE one, traded BY HAND. TradePro places nothing into the live account —
"we are not placing any auto trade into live account". SLV is not a phantom; it
is a legitimate paper position. The two books are disjoint BY DESIGN and no
amount of reconciliation should make them agree.

And the gates were never gating the screen anyway: options_screen.py:1486
passes `capital_gates=False` — "the SCREEN answers 'is this a good trade?', not
'can I afford it?'". Capital limits bind only the autonomous paper wheel, which
is the split already specified in the project notes. `already_in_book:false`
for XOM is CORRECT for the paper account.

DO NOT point the paper ledger at live broker positions. It would merge two
deliberately separate accounts and corrupt the execution record being built
precisely because no platform provides that data for free.

WHAT IS REAL, stripped of the error: on 30 Aug the post-earnings screen offered
MRVL 195 PUT while the LIVE account was already short MRVL Sep18'26 195 PUT.
Nothing is broken — the paper system does not know about the live book and
should not — but someone reading candidates to place manually would want that
surfaced. That is an INFORMATIONAL overlay on the live account, clearly
labelled and never a block. A feature, and only if the owner wants it.

### RANKED

1. Repoint the option chain from yfinance to IBKR — unblocks trading
2. Stop incidental fetches overwriting lane telemetry — until then NO readiness
   verdict can be trusted, including the green ones
3. Apply the weekday adjustment to options_screen staleness
4. ~~Reconcile the book against get_ibkr_positions~~ RETRACTED — see above.
   The paper and live books are separate by design. Do not merge them.
5. Revisit the IV/HV hard gate (design call, after 1)

### UNRELATED, and DONE this session — index short strangle

8 markets live (SPX/XSP/SPY, NDX/QQQ, BANKNIFTY/NIFTY, GOLD), deployed to
Lambda and verified from the live function. Two corrections worth knowing if
you touch it:

* THE GATE WAS READING THE FUTURE. It filtered on the same day's vol close
  while entering at that morning's open. Corrected to a one-session lag; mean
  return fell 10-17% everywhere and SPY's worst day went -0.80% -> -1.89%.
  Every threshold tightened. India's 12.5 was already right.
* Thresholds are now COMPUTED, not chosen — largest gate admitting zero trades
  in any declared crisis window. It reproduces SPY's hand-picked 14 exactly,
  which is why it is trusted; it also caught that GVZ<=16 (my guess) traded
  through 31 sessions of the 2022 bear.

Six MCP tools now expose the suite (94 total). Also removed two tools that were
registered TWICE — FastMCP lets the last win silently, so the first of each
pair was dead code that still looked live.

---

## 30 Aug 2026 — OPTION PRICING: the chain is a PROGRESSIVE SNAPSHOT, read once

**For the options lane. Diagnosis only; owner has assigned the fix elsewhere.**

The IBKR session serving option data is `mode: paper`, `authenticated: true`.
It is NOT dark. The chain endpoint returns fields PROGRESSIVELY, and
`fetch_chain_g3` reads it exactly once.

Identical requests, same expiry, seconds apart, live against the deployed API:

    first probe:  6 legs   bid/ask 0   OI 0   IV 0    <- ONLY conId/strike/right
    call 1:       6 legs   bid/ask 6   OI 0   IV 0    <- bid/ask arrived
    call 2:       6 legs   bid/ask 6   OI 0   IV 0
    call 3:       6 legs   bid/ask 6   OI 0   IV 0

The first read carried nothing but contract identity — every quote field null.
The next read had bid/ask on all six legs. IBKR primes the snapshot on request
and serves it on a LATER call.

**THIS IS THE SAME SHAPE AS THE FILL BLINDNESS** (see
project_ibkr_fill_blindness_root_cause: "the blotter read asked ONCE, IBKR
primes it and says snapshot:false"). The chain path repeats it exactly: ask
once, get an unprimed response, give up.

It explains, at last:

* `chains_g3` logging "N leg(s) but none carried quote data ... returning None"
  and falling through to yfinance. Not a dark feed — an UNPRIMED one.
* Open interest that was inconsistent between identical calls minutes apart
  (SPY median 654, then 0, same expiry). Fields land on different calls.
* The 37 consecutive DEGRADED options_screen runs.

So the monthly-expiry fix committed earlier today (932c178) is real but
SECONDARY. The primary fault is that a single read of a progressive snapshot is
unreliable for every field, not just OI.

**SUGGESTED FIX** — treat an all-null chain response as UNPRIMED rather than
absent: re-poll once after a short delay, and only then fall through to
yfinance. Cheap, and it targets the mechanism instead of the symptom.

**TWO THINGS NOT ESTABLISHED, do not assume either way.** OI and IV never
arrived in any of these four calls, yet both DID come back earlier the same day
for the same names — so it is unknown whether they are genuinely unavailable on
a paper/no-OPRA session or merely slower to prime. And all of this was measured
on a Sunday with the market shut; cache behaviour may differ entirely with live
quotes. A Monday run distinguishes them, and that measurement should come
BEFORE any conclusion about entitlements.

---

## 30 Aug — the chain retry ALREADY EXISTS, and is capped at one. Do not add a second.

Follow-up to the progressive-snapshot diagnosis above. Before anyone implements
a re-poll, read this: **there is already one, and the Python path is not a
separate client.**

**There is ONE path to IBKR, not two.** `chains_g3.py` does not talk to IBKR — it
calls `GET /api/ibkr/chain/{symbol}` on our own API, which owns the session. So a
retry added in Python would sit ON TOP of the C# retry below, on the same
request, with neither layer owning the decision. Three layers of retry and a
worse pacing budget. Owner's ruling, 30 Aug: *"i still do not want 2 diff path of
data access ... one api shd be there for core data access"* — we already have
that; the fix belongs in the one place.

**`ChainEndpoints.cs` has primed since 2 August** and the cap is deliberate:

    // Same warm-up quirk as the spot snapshot above, live-verified 2 Aug
    var quotesResult = await ibkr.GetOptionSnapshotBatchAsync(conIds, ct);
    // One retry, not two (12 Aug): the second retry's extra snapshot
    for (var attempt = 0; attempt < 1 && QuotesStillCold(quotesResult.Quotes); attempt++)

So the defect is NOT a missing concept. It is an **under-tuned bound**, capped at
one to protect the pacing budget on 12 Aug — before anyone knew fields prime at
different rates. The measurement above shows bid/ask arriving on call 2 with OI
still absent by call 4, so one retry can never reach open interest.

**Two changes, one place:**
1. Raise the `attempt < 1` bound.
2. Make `QuotesStillCold` judge on OPEN INTEREST, not just bid/ask — otherwise it
   goes cold-to-warm the moment bid/ask land and abandons the field the wheel's
   liquidity gate actually rejects on. `GetOptionQuotesAsync` (caf5b4f) already
   polls until the answer stops improving and logs which fields never arrived;
   `QuotesStillCold` needs the same standard.

**Still unestablished, and do not assume either way:** whether OI/IV are absent
on a paper session or merely slower to prime. Everything above was measured on a
CLOSED SUNDAY. Monday's live run settles it. **Nobody should buy an OPRA
subscription to fix what may be a priming bug.**

---

## 30 Aug — the chain resolves by MONTH; IBKR needs the EXACT EXPIRY

Why the chain returns only ~4 strikes and cannot reach a 10% OTM put.

`ChainEndpoints` picks strikes nearest spot and resolves each with
`secdef/info?conid=…&month=SEP26&strike=…&right=P`. Measured on MRVL:
**52 of 56 strikes failed with `"No Contracts retrieved"`.** Only 212.5–220.0
resolved — the four nearest spot.

SEP26 contains FOUR expiries (0904, 0911, 0918, 0925). A month-level query is
ambiguous, and IBKR answers for the nearest weekly, whose listed strike band is
narrow. The far strikes are not missing from the market — they are missing from
*that* expiry.

Proof: the same 195 put resolves fine through an EXACT expiry. The live MCP call
used `483492393@SMART/OPT/SMART/20260918/MRVL/1` and returned conid 873598611,
which then priced at bid 3.30 / ask 3.70 / IV 57.2% / OI 2,472.

**Fix direction:** `GetOptionContractsAsync` should resolve by exact expiry, not
month. `maxStrikes` is NOT the problem — 20 and 60 return identical output
because the cap is applied before resolution and resolution is what fails.

**Consequence today:** the puts screen cannot price a 10% OTM put and says so on
the row rather than pricing the wrong contract (54d7654). That guard exists
because the first version silently priced a 5-day 212.5 put against a 30-day
194.96 target — real numbers, wrong contract.
