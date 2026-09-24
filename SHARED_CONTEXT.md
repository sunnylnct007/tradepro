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

---

## 2026-08-30 — the puts screen prices. Two of my own diagnoses above were WRONG.

MRVL now reads across, live from IBKR on the paper account:

    sell the 195 put, expiring 2026-09-25 (26 DTE)
    bid 4.45 / ask 5.00 · mid 4.72 · premium $472
    yield 2.42% on collateral · annualised 29.4%
    break-even 190.28 · IV 56.2% · delta -0.22 (22% assignment)

Verified end-to-end: priced by the CLI, pushed, served by the live API at
`/api/today-setups/post_earnings_puts/latest`, and rendered by the deployed
bundle (confirmed on the box — the UI sits behind basic auth, so CI green was
not accepted as proof).

### Correction 1 — "resolve by exact expiry, not month" was wrong

The section above concluded `GetOptionContractsAsync` had to stop querying by
month. It did not. `secdef/info` takes a **month** and returns every expiry
within it; the 52-of-56 strike failure was a missing `exchange=SMART` on the
`secdef/info` and `secdef/strikes` calls (bca6b43). With that one parameter the
chain went from **4 strikes (212.5–220.0) to 40 (167.5–265.0)**, reaching the
195 target. I had written the wrong fix direction into this file as if settled.

### Correction 2 — "OPRA is unsubscribed" was wrong, all week

IV, bid, ask and greeks are all served. The market-data probe returns the
*subscribed field set*, not the set you asked for, so a field absent from the
probe is not a field IBKR withholds. Several days of "we have an OPRA problem"
were spent on this. The owner challenged it repeatedly and was right each time.

### The two bugs that were actually left

1. **`maxStrikes=1` on the expiry-discovery call.** `availableExpiries` is
   derived from the legs the chain actually RESOLVED, not from a separate
   listing — so asking for one strike under-reports it:

       maxStrikes=1 -> ['20260904','20260911','20260918']
       maxStrikes=2 -> [..., '20260925']

   The 26-day expiry — four days from a 30-day target — was invisible, the
   nearest looked like 19 days, it missed the ±10-day tolerance, and the row
   reported no expiry near the target. **A limit created by my own cheap
   discovery call, not by the listing.**

2. **No poll for bid/ask.** Per IBKR's spec the first snapshot call for a conid
   is a PRE-FLIGHT that "will not deliver any data", and option legs are freshly
   subscribed the instant we resolve them. So the first read returns IV and
   greeks (computed server-side) with bid/ask still empty — giving up there
   produced "chain returned the leg but no bid/ask" on a contract IBKR quotes
   perfectly well. Now polls 4× at 2.5s.

Both in `post_earnings_puts.py` (9d15ad9). UI columns in 00f7443 — the API had
carried premium/yield/delta/IV since the pricing layer landed and the desk
rendered **none** of it, so the screen looked empty even once the data was there.

### Standing warning

The strike/expiry tolerance guards stay. They are the reason this took another
day instead of shipping a wrong number: the first pricing version silently
priced a 5-day 212.5 put against a 30-day 194.96 target. Real premium, wrong
contract. **A screen that prices the wrong contract is worse than one that
prices nothing.**

---

## 2026-08-31 — OPEN INTEREST WAS NEVER THE PROBLEM. The wheel screen works.

**The board went from 1 eligible of 82 to 18**, GOOGL among them at 15.1%/yr on
6,510 open interest. Nothing about the strategy changed; the inputs stopped
being wrong.

### Field 7638 IS open interest, on the PAPER account

This file and `IBKRResponseParser` both carried the claim that 7638 "was a GUESS
and it is WRONG ... not served on this cpapi session at all". **False.** Same
contract, same moment:

    paper cpapi, conid 904441116 (XOM 155P exp 2026-09-04):  7638 = "868"
    live account, option_open_interest:                      putInterest = 868

Two ordinary bugs, no entitlement issue, no OPRA problem:

1. **We dropped abbreviated numbers.** IBKR sends large values as `"9.21K"` and
   `decimal.TryParse` rejects it — exactly as it rejected `"57.2%"` for IV. So OI
   went null on the MOST LIQUID contracts, which are the ones a liquidity gate
   most wants to pass. Measured live, one chain call:

       XOM  [548, 868, 857, 759, 619, 119]   all parsed
       SPY  [null x6]        raw = "9.21K", "9.09K", "6.80K"

   Fixed in `DecLoose` (K/M/B) — 7e5abbc. After: **39 of 40 legs** carry OI,
   SPY 8/8, NVDA 22,500.

2. **We tested with the market SHUT.** OI served for **1 of 6** contracts closed,
   **11 of 12** open. The "intermittency" was a closed-market artefact.

Median OI across the board went **40 -> 385**. The old Yahoo capture read XOM at
58 against a true 868 and was doing most of the rejecting. Bid-ask is now the top
rejection reason, which is the signal we can actually verify.

### The one rule that would have prevented all of it

**An empty field from IBKR carries NO information.** It can mean not-yet,
not-entitled, asked-wrong, or genuinely absent, and those are indistinguishable
without independently-known truth. Every IBKR saga this month is the same error:
IV as `"57.2%"`, the chain at 4 strikes (missing `exchange=SMART`), empty bid/ask
(documented pre-flight), a missing expiry (our own `maxStrikes=1`), and now OI.

Establish ground truth FIRST, then probe. A probe with a known answer is
evidence; a probe without one is folklore.

### Provenance is what made it safe to change

`MarketContext.open_interest_source` + `cfg.oi_blocking_sources` (9c458d3,
corrected 52d34c5). OI may only REJECT when its source is IBKR; anything else
informs only and says so on the row. It refused to trust the Yahoo number in the
morning and trusts the IBKR one now, with **no code change in between**.

CAUTION: the first version listed `"g3_ibkr"` — a string invented rather than
read. The real label is `"g3"`. Guessing an identifier is the same failure as the
7638 comment, one layer down.

### FIVE cry-wolf labels walked back in two days

Every one fired so often it stopped meaning anything:

| Label | Fired on | Actually |
|---|---|---|
| `0G/216S/28B` harvest | 10 identical runs | counting a session that had not opened |
| `FALLBACK bars` | 73 of 82 rows | ONE agreeing Yahoo close in twenty |
| deploy-drift alarm | permanently | API only rebuilds for backend/frontend |
| "IBKR data is DARK" | after every restart | true, but self-inflicted by mid-session deploys |
| `force-refresh` "inert" | — | it served cache and reported GOLD |

**A channel that is always loud is the same as one that is silent.** The
materiality rule now used in two places (missing bars, mixed providers) is the
pattern: keep the detail, move the GRADE only when it matters.

### force-refresh was never broken — it failed QUIET

    ✓ TSLA   9/9 bars   🥇 gold   source=cache

...printed while IBKR was unreachable. Tick, GOLD, exit 0, bars untouched. The
grade was accurate (it grades what is ON DISK) but the RUN did nothing. IBKR
market data flaps every ~15 minutes (19:05 ok, 18:50 degraded, 18:35 ok), and the
run landed in a dark window. **This cost a wrong diagnosis reported to the
owner.** Now marked `!` with the reason, and the run log records `partial`
(681ff8b). Ordinary cache hits stay silent — deliberately narrow, so it does not
become the sixth cry-wolf.

### Traps found today

- **`tradepro-bar-cache-harvest --from ... ` with no `--symbols` covers TWELVE
  hardcoded names**, not the 244-name universe. It says `symbols=12` in the
  header, but "run the backfill" does not do what it sounds like.
- **`--force-refresh` WITHOUT `--ibkr-only` will overwrite IBKR bars with
  Yahoo.** I did this to TSLA while investigating (21 of 41 bars became
  yfinance, including a bar for an unfinished session) and repaired it to 100%
  ibkr_web. The script's own warning says exactly this. Use the guarded CLI path,
  never a raw `store.get(force_refresh=True)`.
- **Deploying restarts the API and kills the IBKR market-data session.** Two
  sessions deployed inside eleven minutes today. Worth a rule: no mid-session
  deploys unless the change is needed FOR that session.
- **The MCP connector also takes the market-data session** (one per account).
  Using it to establish ground truth is legitimate; doing so silently is not.

### Still true, and not fixed by any of the above

- The wheel's **v3 backtest verdict DO NOT FUND stands.** Today fixed the
  screen's INPUTS, not the strategy's mechanism.
- **IV-Rank reads `n/a` everywhere** — 18 days of the 60 needed. The IV/HV bridge
  is standing in, and says so.
- **Momentum's 19 candidates have never been reviewed** for evidence by anyone.

---

## 31 Aug 2026 (evening) — OPTIONS L4 GRANTED; the exit path never existed (data/platform lane)

**READ THIS BEFORE TOUCHING IBKR POSITIONS OR OPTION ORDERS.**

**1. Options permission went L3 → L4 in one evening.** Every call leg that
"failed silently" all week was IBKR correctly refusing a level-3 account:
short puts and spreads yes, **naked calls no**. Our order construction was
never at fault. The owner requested L4 and it was **GRANTED the same evening**,
so the strangle now runs as designed and the iron-condor rewrite is off.

Check `Settings → Account Settings → Trading Permissions` FIRST next time. A
refusal, like an empty field, carries no diagnosis — I inferred the tier from
the error text and stated it more firmly than the evidence supported.

*Consequence:* L4 restores the **UNCAPPED call tail**. Nothing now caps the
risk the vol gate exists to dodge, so the overnight tripwire matters more.

**2. IBKR POSITIONS LIE TWO WAYS.** Both fixed, both worth knowing:
- IBKR serves `/portfolio/{acct}/positions` from **its own cache** that never
  self-clears. Three puts were bought back, all three orders returned Filled
  with `remainingQty 0`, and positions reported them OPEN with byte-identical
  P&L for minutes. Use `?fresh=true` (invalidates first) for anything asking
  "did it close?".
- A **CLOSED position still returns, with quantity 0**, and we rendered it as
  open. Now filtered at the API.

This mattered beyond display: both option guards VERIFY against positions
before placing, so a stale read decided whether an order went out at all.

**3. TradePro could OPEN an option it could not CLOSE.** Every path assumed a
matched pair — `/strangle/close` buys BOTH legs, so on a put-only book it
would have BOUGHT A CALL we never owned. New:
`POST /integrations/ibkr/option-leg` (one contract; a BUY is REFUSED unless the
broker confirms we are short it) and `POST /integrations/ibkr/options/flatten`.

⚠ **`options/flatten` closes EVERY short option at the broker** — wheel and
hand-placed included. The strangle auto-close deliberately does NOT use it; it
matches configured markets and closes leg by leg.

**4. The profit target was PER LEG.** Harmless while only puts filled. With
both legs live, one leg hitting 50% decay would be bought back and leave the
other — the losing one — NAKED. Now judged on the pair's combined credit.

**5. Nothing linked a DECISION to what EXECUTED.** Owner asked "did the
strangle work or not" and the platform could not answer from its own records;
both numbers were reconstructed from the broker by hand. Migration **072** adds
placement + exit columns (all nullable); `POST /api/strangle-decisions/execution`
attaches on the decision's own key and **404s an orphan** rather than inserting
a fill with no reasoning. `partial` and `shadow` are stored separately — a
one-legged fill is a NAKED short, not a strangle.

**6. BUILT BUT NEVER WIRED, again.** `strangle_manual_trade` (migration 070)
had no reader, no writer, no MCP tool, so the owner's trade had nowhere to go.
Now `GET/POST /api/strangle-manual-trades` + `/summary` + 3 MCP tools. An audit
found 4 more tables with no backend consumer: `broker_ticker_map_suggestions`,
`risk_velocity_window`, `system_alerts`, `schema_data_migrations`.

**7. CI: two lambda deploys RACED.** AWS allows one in-flight update per
function; the loser left the Lambda a commit behind `main` — the exact drift
that workflow exists to prevent. Now serialised with a concurrency group plus
wait-then-retry.

**RESULTS, stated honestly.** 3 short puts closed **+229.11** (mark at close —
there is no executions endpoint for options and `avgPrice` is null). BANKNIFTY
manual **+396**. **ZERO strangles executed** — every US fill was put-only, so
none of this is strangle performance.

**STILL UNVERIFIED — needs an open market:**
- whether the **PAPER clone** picked up L4 (one 1-lot call at the open settles it)
- the **auto-close round trip**: parse → decide → place → confirm filled. Dry-runs
  against a flat book exercise the read path and nothing else.

**Traps for whoever picks this up:**
- The IBKR **pause is in-memory — a DEPLOY clears it** and steals the session
  back mid-portal-login.
- **Never smoke-test a mutating route.** I used `POST /options/flatten` as a
  liveness probe; it placed three real orders.
- Wait-loops must key on the **commit SHA**, not the workflow name — mine
  matched a previous deploy and called a not-yet-deployed route a failure.
- A **Python-only** merge produces no `aws-build-push`/`aws-redeploy`; watch
  `aws-lambda-jobs` instead or you will wait forever.

## 7 Sep 2026 (00:xx) — PER-SYMBOL WATCH FRAMEWORK LIVE; first session is TODAY

**8 symbols on the preearnings/SwingWatch board** (MU, SNDK, WDC, STX, NVDA,
MRVL, CRDO, PLTR). Engine `tradepro-preearnings-watch`: Lambda cron(0/5 8-21
MON-FRI) + Mac plist (shared settings-kv dedupe — no double alerts), IBKR-first
bars (store 5m→15m resample; yfinance prepost fallback, labelled), options
term-structure context from the chain capture, decision-first rows with named
reasons + what-changes-this triggers, MCP tools preearnings_status /
preearnings_evaluate / get_option_chain_context. UI: run Pre-Earn button;
symbol click opens the chart rail.

MU: WATCH (TOLERATED rollover) — armed 958 band reclaim / 1050 breakout
one-shot / gap guard ~890; print 2026-09-30 AMC owner-confirmed (phantom 9/21
row DELETED from earnings_calendar). Options: +9.7 IV pts event premium,
P/C OI 9.05, put wall 980-1000. NVDA + PLTR QUALIFIED watch; SNDK/STX/MRVL/
WDC/CRDO BLOCKED with exact reasons on-row.

TRAPS: settings-kv PUT stores the RAW BODY as value (do not wrap). A rarely-
touched symbol's first store read can be a stale partition — S3 read-through
refreshes on second touch (SNDK 1554→1740). Scale-invariance invariant in
docs/MULTI_SYMBOL_ARCHITECTURE_ADDENDUM_V1.md: engine lookbacks ≤63 sessions,
ATR-multiple thresholds only; owner-armed dollar levels exempt.

Budgets are PROVISIONAL_PAPER_DEFAULTS (swing $1k / core gap $2.5k / intraday
$300), account IBKR_PAPER. Earnings workstream CLOSED (Q3 + both vetoes
failed gates — do not re-raise). Funding gates 089a8ec await D1-D3.

## 8 Sep 2026 — the close job was blind to its own placements (ROOT CAUSE)

Four pairs opened 13:53Z were flattened at 14:00Z, seven minutes old. Same
shape as last week. Root cause was NOT the flatten logic:

  UPSERT keys on COALESCE(exchange_date, as_of)  -- the session TRADED
  SELECT filtered on as_of                       -- the session the gate READ

Migration 073 fixed the write path on 1 Sep and left the read path on the other
column. Identical on an ordinary Tuesday. 7 Sep was Labor Day, so US rows for
exchange_date 09-08 carried as_of 09-04; the close job asks days=3, its window
began 09-05, and EVERY US row fell outside it. It read zero placed rows against
four live pairs and flattened them. India was never affected — NSE traded 7 Sep.
Fixed at all THREE sites (row SELECT, /summary, and the upsert already correct).

Shipped today: e746679 select key · c49b048 close states its reason (the live
close printed no reason at all, which is why this took a week) · 31088ad live
desk P&L.

LIVE P&L now exists: GET /strangle-decisions/pnl, MCP get_strangle_live_pnl,
plus "Still open — live" and "Desk total" on screen. total is NULL when the
open half cannot be read — that is UNKNOWN, never flat.

Also learned: the IBKR option chain is EMPTY early in the session. SPY/QQQ
failed at 13:52Z (22 min after the open) with "NO strikes for conid" and
resolved fine at 15:39Z, same conids, same month. Not an entitlement problem.
GOLD (conid 51529211) still fails mid-session — separate issue.

OPEN / needs an owner call:
  - the decision log holds ONE row per market per session, so two round-trips
    in a day collide. Today SPX shows credit_actual from the 15:39 entry with
    realised_pnl from the 13:53 one. Blocks re-entry.
  - placement cron fires ONCE (13:52Z). Nothing re-enters after an exit.
  - 13:52Z is 22 min after the open, which is when the chain is emptiest.

## 2026-09-08 (evening session — swing σ + scout)
- SWING ENTRY IS NOW −2.25σ (was −2.5): pre-registered band study, `strategies/SWING_SIGMA_BAND_GATES_V1.md` + RESULT. Band C (−2.25..−2.0) FAILED the tail gate — do not widen further without a new gates doc. Trade records split at 8 Sep.
- Scout has a second lens: repeat movers (top-8 gainers ≥2 of last 5 sessions, above EMA20) — INTC-shaped V-recoveries. State in `preearnings_scout_state.movers_history`; thresholds in `preearnings_scout`.
- DO NOT clear `scout_last_run` casually: before commit 32c3c7d that froze the whole watch board for an hour (sweep outlived the 5-min tick and re-armed itself). Marker is now written BEFORE the sweep; sweep is store-only + 120s budget.
- Yahoo got throttled tonight from repeated batch calls; extras (NBIS/IREN/SMCI/CRWV/ROIV/SMR) ride yfinance and go last in the sweep.

## 2026-09-10 late — Koyfin direction (owner)
- Owner: move toward Koyfin-grade finished product ONCE signals are trusted; sequencing his call, not now.
- Shipped tonight: company profile block in every row expansion (name/sector/industry/staff, business summary, 52w range bar, analyst consensus target labelled context-only) + PEG in vitals. Data = key_stats cache, no new APIs.
- Phase candidates for the Koyfin push (NOT started): normalized compare charts (symbol vs SPY vs sector ETF overlay), sector heat strip, full-page symbol profile route (chart + financials + news + our signals history), watchlist screens. Sequence AFTER signal trust per owner.

## 2026-09-11 — swing execution was DEAD 9–11 Sep (fixed)
- T212OrderRouter rejected all non-MARKET orders at the top of _handle_approval. It doubles as the OMS transport for IBKR_PAPER/IG_DEMO, so every swing LIMIT entry was dropped: 208 orders, SHOP/DASH/BLK/SBUX/ARES/SWK. Guard moved to just before T212's own HTTP call. Tests: tests/test_limit_orders_reach_the_oms.py.
- IT LOOKED HEALTHY because the same runs mirror account positions and record EXISTING executions as ledger fills. To check this lane trades: GET /api/oms/orders/{id} and require a non-null brokerOrderId. Not logs.
- An OMS /approve 409 means EITHER idempotency dedupe OR a pre-trade refusal (system_state, RiskGate market_closed/size/velocity). Router now prints the reason and warns on refusal.
- Two strangle tests red on main (test_strangle_execution_link.py, KeyError 'body'/'partial'), from d7b1261 — flagged to tradepro-7f, not touched.

## 2026-09-11 — build/test discipline (my breakage, their catch)
- `npx vite build` does NOT typecheck. Use `npm run build` (tsc -b && vite build) before any frontend push. A green vite on untypechecked code blocked ALL deploys for 9h on 10 Sep (CandidatesView TONE undeclared).
- Run `uv run pytest tests/ -q` in strategies/ before merging to main. main is shared; a red build blocks both sessions.
- test_strangle_execution_link's 2 failures were NOT a regression: place_paper refuses for the first 20 min of a session (dc5b7a2), returning early with no POST and no `partial` key. Tests fail only during that window. Any new test driving place_paper MUST pin the clock. Fixed by tradepro-7f in #136.

## 2026-09-13 — the MCP connectors were never going to work (built the missing piece)

`TradePro-Web` / `tradepro-Aws` point at `https://tradepro.showsoldprice.com`
— the SPA origin, behind nginx Basic Auth. `/mcp` and `/sse` answer 401,
so tools/list never runs and Claude renders "connected, no tools".

Behind that there was nothing anyway: **the MCP server has only ever had a
stdio transport**, spawned by uv on the Mac. It works with the desktop link
and nowhere else. Not a config fault — a missing component.

Two wrong diagnoses got spent on this first. For the record:
  - NOT OAuth / stale registration / a per-chat toggle.
  - NOT a dead cloudflared. `~/.cloudflared/config.yml` serves openclaw +
    ollama only; tradepro.showsoldprice.com resolves straight to the EC2
    elastic IP (16.60.201.137). If it HAD been an orange-clouded tunnel,
    a dead cloudflared gives a 1033 page, not ECONNREFUSED.
  - The Saturday `Connection refused` was the box stopped for the weekend.
    `aws-scheduled-start.yml` is `50 12 * * 1-5`. Normal, by design.

Shipped 44e9aec on live-main (NOT yet on main):
  - `tradepro-mcp-http` — streamable-HTTP, read-only, `mcp` compose service.
  - Caddy routes `/mcp*` straight to it, bypassing the nginx Basic Auth gate.
  - stdio surface UNCHANGED; the Mac keeps all 113 tools.

READ-ONLY IS ENFORCED, NOT A CONVENTION. 12 tools stripped (113 → 101):
approve/reject_paper_order, set_paper_placement_mode, close_option_leg,
flatten_short_options, run_paper_session, apply_paper_override (has
FORCE_CLOSE), configure_paper_llm_gate, update_paper_strategy_config,
record_strangle_manual_trade, run_comparison, and ibkr_fetch_bars — that
last one needs the SINGLE IBKR market-data session the live desk holds.
Two startup guards abort rather than serve a surface we can't vouch for:
a stale MUTATING_TOOLS entry (= someone renamed a mutating tool) and a
mutating-verb tripwire. aws-build-push runs those tests BEFORE pushing
the image.

Auth = secret path segment: server answers on `/mcp/<token>`, 404s bare
`/mcp`. claude.ai's connector UI takes a URL and nothing else, so the
secret rides in the URL. Accepted for a surface that cannot trade —
do NOT widen the surface without replacing this with real auth.
GH secret `TRADEPRO_MCP_PATH_TOKEN` is set; it reaches the box via
`aws-set-env`.

NOT LIVE YET. Blocked on two owner steps, both needing creds this session
did not have (infoccit-admin SSO expired):
  1. `aws ecr create-repository --repository-name ccit-dev-tradepro-mcp
      --region eu-west-2` — aws-build-push FAILS without it.
  2. merge live-main → main, then aws-build-push → aws-set-env → aws-redeploy.

Endpoint inherits desk hours: **up only Mon–Fri from 12:50 UTC**. Owner
confirmed the weekend shutdown is deliberate and `aws-start` covers ad-hoc
access. Full runbook: `docs/REMOTE_MCP_ENDPOINT.md`.

## 2026-09-13 — I TOOK THE SITE DOWN for ~7 min deploying the MCP service

Sunday, markets closed. 13:36Z → 13:43Z. `/health` 200 again at 13:43Z,
all four containers healthy.

**Cause: a new compose service can take the WHOLE STACK down.** The EC2
role's `ccit-dev-tradepro-ec2-ecr-pull` policy scopes `ecr:BatchGetImage`
to a Resource LIST naming only the api and frontend repos. The new
`ccit-dev-tradepro-mcp` repo was not on it. Creating the ECR repo is NOT
enough — the instance role must be allowed to pull from it.

The pull was denied, and `aws-redeploy` runs
`up -d --remove-orphans --force-recreate`, which **tears containers down
BEFORE it pulls**. So the failure did not just skip the new service, it
left NOTHING running. 443 stopped answering — the exact symptom this
whole workstream started from, which is its own lesson about reading a
connection-refused.

Fixed so it cannot recur (commit on main):
  - `mcp` is behind `profiles: ["mcp"]`. Plain `docker compose up -d` —
    what every deploy runs — skips it. Start it explicitly:
    `docker compose --profile mcp up -d`.
  - caddy NO LONGER `depends_on: mcp`. The edge comes up and serves the
    site whether or not MCP is running; `/mcp*` 502s meanwhile.

STILL TO DO to turn the endpoint on (needs an owner with IAM write —
this session was denied the call):
  1. Add `arn:aws:ecr:eu-west-2:108703420282:repository/ccit-dev-tradepro-mcp`
     to the Resource list of inline policy `ccit-dev-tradepro-ec2-ecr-pull`
     on role `ccit-dev-tradepro-ec2-20260510220637811100000002`.
     NOTE: that role is terraform-managed — change it in TF, or the next
     apply reverts it and the endpoint dies at the following deploy.
  2. `docker compose --profile mcp up -d` (or add the profile to redeploy).
  3. Verify on the LIVE url: bare `/mcp` → 404, `/mcp/<token>` GET → 406.

Image built and pushed fine: `ccit-dev-tradepro-mcp:latest` is in ECR,
arm64, and the read-only surface test gated it. GH secret
`TRADEPRO_MCP_PATH_TOKEN` is set and now written to /opt/tradepro/.env.

## 2026-09-13 LATE — remote MCP endpoint is LIVE

Verified against the LIVE url, not CI:

  /health              -> 200
  /mcp                 -> 404   (bare path, no token — by design)
  /mcp/<token>  GET    -> 406   (bound and routing)
  /mcp/<token>  POST   -> 101 tools, ZERO mutating
  tools/call get_health -> real payload from http://api:5080/health/details

Connector URL is in the GH secret TRADEPRO_MCP_PATH_TOKEN; the full URL
is `https://tradepro.showsoldprice.com/mcp/<token>`. Update the EXISTING
`TradePro-Web` / `tradepro-Aws` connector entries — both still point at
the SPA origin and will keep failing until changed.

What unblocked it: the EC2 role inline policy
`ccit-dev-tradepro-ec2-ecr-pull` now lists the mcp repo. Starting it with
`--profile mcp up -d` did NOT recreate the other containers (they stayed
"Up 7 hours") — no second outage.

⚠ TWO THINGS THAT WILL BITE LATER:
  1. **The IAM change was made with the CLI, NOT terraform.** That role is
     TF-managed. The next `terraform apply` reverts the Resource list, the
     pull starts failing again, and the endpoint dies at the deploy after
     that. Put the mcp repo ARN in the TF module.
  2. `aws-redeploy` ran `up -d --remove-orphans` WITHOUT the profile, which
     would have removed tradepro-mcp on the next deploy. Fixed: redeploy now
     brings the site up first, then starts mcp best-effort — an MCP failure
     prints a warning and CANNOT fail the deploy or take the site down.

Endpoint still inherits desk hours: up only while the EC2 box is (Mon–Fri
from 12:50 UTC, or an ad-hoc `aws-start`).

## 2026-09-15 — terraform pipeline BACK, four-month gap closed

Owner ran the apply. `terraform plan` is clean both locally AND in CI:
"No changes. Your infrastructure matches the configuration."

The last two faults only CI could find:
  - `secretsmanager:GetResourcePolicy` — the provider calls it on EVERY
    refresh of an aws_secretsmanager_secret, even with no policy set.
    INVISIBLE locally: an admin profile has it implicitly, so the laptop
    plan was green while the deploy role died on the same config. "Works
    for me" is not evidence the pipeline works — dispatch it.
  - **Version skew.** CI pinned TF 1.7.5; state stamped 1.15.1 by local
    runs. `required_version = ">= 1.6.0"` allowed both, so nothing
    errored — they just resolved different providers. Local said "No
    changes"; CI said "4 to change" (random_password.rds, the RDS
    instance, the secret version, an access key) — ALL phantom, every
    attribute unchanged. Dangerous because a diff naming random_password
    + the db instance reads as a password rotation at a glance. CI now
    pinned to 1.15.1 and the two agree. Whatever writes the state sets
    the floor; bump CI and the local toolchain together.

Drift deliberately CODIFIED rather than reverted (owner call):
  - IMDS `http_put_response_hop_limit` stays **2**. hop_limit 1 confines
    instance-role creds to the host; containers need 2. Ours use static
    keys from .env so 1 was probably fine — not good enough to risk.
  - Auto-stop schedule stays **DISABLED**, now via `schedule_stop_state`.
    Start/stop is the aws-start / aws-stop workflows.

Also: `terraform apply` on push is GONE. Push plans and stops; apply is
workflow_dispatch only. The `aws-prod` environment has ZERO protection
rules despite the workflow claiming it gates on approval — still worth
adding required reviewers.

MCP endpoint verified again after all of it: 101 tools, zero mutating,
bare /mcp 404.

---

## 2026-09-16 (RESEARCH + DATA): desk-wide reanalysis — measured live, not recalled

Owner asked for a fresh read of the whole application. Every number below came
from the live API and the live OMS on 16 Sep, not from memory or a doc.

### The flagship is idle, and that is the design working
Last 40 days, per market: **26 evaluated, 0 traded, 26 declined** — SPX, XSP,
SPY, QQQ, NDX, GOLD alike. Live reason, verbatim:

    ^VIX 17.20 is ABOVE the 13.5 threshold — not a low-volatility day
    (trailing 25th pctile 16.01)

Under the trailing-quartile rule **16 of those 26 sessions would have traded.**
That is NOT a missed-opportunity figure and must never be quoted as one. The
quartile rule was rejected with evidence at index_strangle_paper.py:60-80 — it
fires ~25% of days in every era BY CONSTRUCTION, and in 2009-16 that meant
selling at a median India VIX of 16.7 while calling it low volatility, which is
exactly the era that lost -427/trade. 16/26 measures what the absolute gate is
SAVING, not what it is costing. VIX ran 14.3-17.8 all period; the gate is
waiting for a regime that has not arrived.

Minor drift to resolve: documented default is `VIX_MAX["US"] = 14.0`, the live
threshold is **13.5** via env override. One of the two is wrong.

### All P&L on the board is from trading AGAINST the gate
17 closed pairs, every one `shadow: true` (deliberate paper fills on refused
days). Total **+$191.54**, and it does not generalise:

    SPX  6 pairs  +207.47
    XSP  9 pairs    -1.92
    QQQ  1 pair     -8.28
    SPY  1 pair     -5.73

Ex-SPX the shadow book is roughly break-even. **17 pairs is not yet evidence
the gate is too tight** — and there is no pre-committed number that would make
it evidence. Set one before the sample grows, or it gets read as whatever the
board wants that week.

BANKNIFTY/NIFTY show 20 traded of 24 — notional only, no Indian broker.

### What actually transacts (live OMS, last 100 orders, 28 Aug - 15 Sep)
    T212_DEMO  ichimoku_equity             FILLED     19
    IBKR_PAPER mean_reversion_swing_ibkr   FILLED     12
    IBKR_PAPER mean_reversion_swing_ibkr   REJECTED   22
    IBKR_PAPER mean_reversion_swing_ibkr   CANCELLED  10
    IBKR_PAPER exec_path_probe             CANCELLED  30

- **A third of recent order volume is plumbing probes** (32 of 100).
- The 22 rejections are NOT the execution outage. All one day (2 Sep), all
  IWM, one cause — "No Trading Permission, Customer Ineligible" — retried 22
  times, **zero brokerOrderId returned on any of them**. One permanently
  unpermitted symbol burned 22 attempts. Needs a don't-retry-a-refusal guard.
- Every broker is IBKR_PAPER / T212_DEMO / PAPER. **Nothing is funded.**

### Scale vs evidence
~196k lines (93k Python, 46.5k C#, 56.5k TS), 130 test files, ~30 loaded
launchd agents, plus Lambda + EC2/compose + Postgres + S3 + MCP. Four plists
carry SUPERSEDED/STOPPED/PAUSED suffixes and were never removed.

13 pre-registered studies. Cleared their gates: mean_reversion_v2,
post_earnings_put_v2, short_strangle_india_v2. Failed or killed: wheel v3,
S/R levels, ICH S/R filter, ICH exit v1, ICH exit v2, momentum v3, Quiver
congress, earnings v2. The gate discipline is why no bad strategy has been
funded — but the standing result is **3 strategies past their own gates, 0
funded**, against a platform that keeps growing.

September's commits are ~80% repair: digest mails all falling back to plain
text, close recording 2 of 3 exits against the wrong round-trip, IBKR's
rejection reason truncated before it said why, a deploy script dying on
unquoted parens, a compose service taking the whole site down, Terraform CI
dead since May with RDS untracked.

### THE FINDING THAT BLOCKS EVERYTHING ELSE
FUNDING_GATES_V1's evidence window starts when failure-visibility is deployed
(done, 5dfa6f7, 5 Sep) **AND paper NAV equals the D1 figure**. D1 is still
OPEN, so **the 6-week clock has never started.** Every day D1 stays open is a
day the clock is not running.

Worse, the two interact: S1/S2 need >=10 consecutive placed-and-closed cycles
and >=12 cycles across >=3 markets in >=6 weeks — and the vol gate has produced
**zero** qualifying entries in 40 days. As written, **the strangle funding
gates are unreachable while VIX stays above 13.5**, however long the window
runs.

### D2 is severable — it is not on the critical path
Checked, not assumed: `YELLOW` appears ONLY in options_screen.py,
quant_engine/options/wheel_backtest.py and quant_engine/options/risk.py. It is
read by the wheel/puts sleeve. **Neither funded sleeve — strangle or swing —
reads it.** So D2 blocks the wheel (already DO-NOT-FUND on its own backtest),
not this book. Recommend striking D2 from the funding blockers, leaving D1 and
a scheduling call in D3.

### RESOLVED SAME DAY — D1 closed, D3 closed with it
Owner set **D1 = $150,000** on 16 Sep. Derivation, both sites of the figure,
and the consequences are in FUNDING_GATES_V1.md. Headlines:

* **D3 closed for free.** $150k is what the paper account already holds, so
  there is no NAV realignment and no paper reset. Had D1 landed elsewhere, D3
  would still be open.
* **D2 is NOT blocking** — `YELLOW` is read only by options_screen.py,
  wheel_backtest.py and options/risk.py. Neither funded sleeve touches it.
  Left OPEN in the doc because striking it is the owner's call.
* **The live swing sleeve moved 100,000 -> 150,000** (a 50% sizing increase on
  an auto-placing lane). Follows from D1 as the doc defines it; reversible in
  one word if a sub-allocation was meant instead.
* **credit_modelled was fixed before the window opened**, not after. Each
  expiry now prices at its own DTE. S3 on the monthly leg reads 94.7%; the
  weekly reads 45% because `iv_used` is a 30-day index applied to a 7-day
  option — a term-structure blindness, not a pricing bug. **Grade S3 on the
  monthly leg only** until the chain lane captures more than one DTE.

### Still open for the owner
Whether shadow cycles may satisfy the RELIABILITY gates (S1-S3, B2, B3 test
plumbing, and shadow fills exercise the identical path) — without this the
strangle gates are unreachable while VIX stays above 13.5; the 13.5-vs-14.0
threshold drift; and a cut list for retired lanes and probe jobs.


## 2026-09-16 (later, DATA+RESEARCH): the three follow-ups, in order

### 1. Shadow cycles now satisfy the RELIABILITY gates — owner ruling
S1, S2, S3, B2, B3 may be graded on shadow fills; **S4 may not**. The split is
the point: those five test PLUMBING (can it place, close, price, always exit,
report its own failures) and a shadow fill is a real paper fill at a real price
through the identical code path. S4 is the only gate asking whether the
strategy makes money, and grading it on days the gate refused to trade would
invert the strategy. Populations are already tagged `shadow: true` at source,
so the split is enforced by data, not by memory. Full reasoning + the standing
17-pair evidence is in FUNDING_GATES_V1.md. Without this the strangle sleeve
could run the whole six-week window with S1 reading zero.

### 2. The "13.5 vs 14.0 threshold drift" was NOT drift — it was a dead constant
Retracting my own 16 Sep finding: **the live 13.5 is correct and always was.**
It is computed by `choose_threshold` in index_strangle_sim — of a half-point
grid, the largest threshold admitting ZERO trades inside any declared crisis
window — and `test_thresholds_are_the_rules_output` already guards it.

What was wrong is that `index_strangle_paper.py` opened with

    VIX_MAX = {"US": 14.0, "INDIA": 12.0}

under a large evidence block, and **rebound the same name ~200 lines later**
from MARKETS. The first binding was dead from the moment the eight-market
config landed, and both its values were stale (US gates 13.5, India 12.5).
Nothing ever misbehaved — which is the danger. The most authoritative-looking
constant in the file contradicted the live gate, and a desk review read it and
reported drift that did not exist.

The existing one-definition test could not catch it: it reads the module AFTER
import and therefore only ever sees the LAST binding. The new
`test_vix_max_is_defined_exactly_once` parses the SOURCE with `ast` and fails
on a second module-level assignment — verified to fail on the old file
("assigned on lines [80, 292]"). Same lesson as ever: **grep the VALUE, not the
name**, and a test that reads the imported module cannot see a shadowed one.

### 3. The cut list was mostly imaginary — correcting the 16 Sep entry
Checked rather than assumed, and two of my three claims did not survive:

* **`exec_path_probe` is NOT a scheduled lane.** It appears in no plist and
  nowhere in the repo. The 30 cancelled probe orders were ad-hoc manual runs
  that already finished. There was nothing to cut.
* **No killed sleeve is still wired up.** I checked all ~30 loaded agents
  against the killed scoreboard (QDB, S/R levels, wheel, Quiver congress) and
  found no orphan. That claim was loose and is withdrawn.
* **The four retired plists were real** and are now gone from
  `~/Library/LaunchAgents`: the two `SUPERSEDED-BY-LAMBDA-2026-09-01`
  strangle lanes (their Lambda replacements are live in `lambda_handler.JOBS`),
  `paper-equity-ibkr.STOPPED-2026-08-22` and `paper-fx.PAUSED-2026-08-24`.
  None was loaded. MOVED, not deleted, to
  `~/.tradepro/retired-agents/cut-2026-09-16/` — reversible.

So the real surface reduction today is four dead files, not a lane retirement.
Worth recording precisely, because "we cut the probe lanes" would have entered
the record as a saving that never happened.


## 2026-09-17 (RESEARCH): the strangle's own chains are finally being captured

### The gap
FUNDING_GATES_V1 S3 is gradeable on the MONTHLY leg only, because the model
prices every expiry off `iv_used` — a 30-day vol index — and on a 7-DTE leg in
contango that overprices badly (XSP 15 Sep: weekly received 245.56 vs modelled
546 = 45%; monthly 94.7%). Fixing it needs a real per-expiry ATM IV.

**`option_quote_daily` held ZERO rows for SPX and XSP.** The two markets this
desk actually places had no captured chain at all — they were never in the
capture universe. SPY/QQQ were present but only ever near 29-36 DTE, because
the lane targets ONE DTE (`--dte 35`) and the expiry just rolls across days.

### The trap, measured not assumed
`MARKETS[m]["index"]` is where SPOT comes from. Against Yahoo on 17 Sep:

    ^GSPC   0 chain expiries     <- the `index` for BOTH SPX and XSP
    ^SPX   52 chain expiries
    ^XSP   43 chain expiries
    ^NSEBANK / ^NSEI  0          <- India, at any provider

A lane reusing `index` captures NOTHING for SPX and XSP and reports success.
So MARKETS now carries an explicit `chain_symbol` (None for India, on purpose —
a missing key would read as an oversight), pinned by
`test_chain_symbol_is_never_silently_the_index`.

### The design, and why the strangle set goes FIRST
`--strangle-dte 7,21` captures the strangle roots BEFORE the wheel walk. The
walk already runs **109 min against a 3h deadline** and backs off to the 300s
max pace under Yahoo throttling — doubling its DTEs would blow the deadline and
truncate the tail silently. Captured first, a deadline overrun degrades the
WHEEL tail (months of history) instead of the strangle data (none). The
strangle block shares the walk's rate-limit backoff rather than absorbing
limits and re-earning them 89 symbols later. Omitting the flag changes nothing.

### First run — mechanism proven, DATA NOT YET TRUSTWORTHY
Live run captured all six roots at both DTEs in ~45s, no rate limits:
^SPX 272 legs @7d + 165 @21d, ^XSP 126 + 47, plus SPY/QQQ/GLD/^NDX.

**But it was run with `--force` OUTSIDE the post-close window, and the IV is
not usable.** On the ^XSP 21-DTE ATM strike, call mid 16.12 vs put mid 5.48 —
C − P = 10.64 where put-call parity demands ≈ 0.18 — and the two legs' IVs
disagree 21.8% vs 7.6%. Each price is self-consistent with its own IV, so these
are quotes from DIFFERENT times: stale pre-market marks. That is exactly what
the capture-window guard exists to prevent, and I overrode it.

So: the plumbing is proven, the numbers are not. **The first trustworthy sample
is tonight's 22:15 in-window run**, which will overwrite today's rows on the
same primary key. Do NOT compute a term structure off today's rows.

Standing caution for whoever picks this up: Yahoo's per-leg `impliedVolatility`
on index chains looks unreliable even before the staleness. Prefer the
straddle-mid construction `preearnings_watch` already uses over the raw IV
field, and only from in-window captures.


## 2026-09-19 (RESEARCH lane): lane-sentry shipped — and its first run caught a live outage

### Owner-requested condition review, headline findings (all measured live)
* Swing on IBKR: 20 days = 11 BUY fills, **0 SELL fills ever**. Root cause was
  the placement window (orders raised outside RTH have filled 0 of 46 times,
  ever, across all strategies) — DATA lane's 567ab8c fixed it same day;
  verified live: daemon now logs `defer-market-shut`, churn stopped at 12:17Z.
* Strangle: 15 Sep 3 placed/13 recorded errors; 16 Sep 16/16 refused-with-
  reason (chain resolution); 17-18 Sep placement DEAD CODE (#149 indentation,
  fixed 96e9df6). **Monday 14:12Z is the first session that can prove both
  sleeves' fixes.** Funding evidence clock stopped since 16 Sep.
* Ichimoku/T212 is the only sleeve with proven round-trips (13 buys, 9 sells).

### NEW: `tradepro-lane-sentry` (16:20 + 00:45 local, Mon-Fri sessions)
Reads the OUTPUT tables through the desk's own API and fails LOUD when a lane
did not produce what the schedule owes — including the exact 17-18 Sep
signature: decision rows present but `placed` NULL on every open-session US
row ("never attempted" vs "tried and refused"). Replay verified: flags 17 and
18 Sep, passes 16 Sep (all-refusals day). One run_log row per beat
(process `lane-sentry`), surfaced on the cockpit RunLogCard.

### Its first live run caught a second outage — MINE
Chain capture was DEAD on 17 and 18 Sep: I updated the Mac plist to pass
`--strangle-dte 7,21` on Wednesday while the flag existed only on origin/main.
The Mac lane runs the LOCAL checkout (live-main), which got the code only late
18 Sep — so argparse died at startup both nights and took the WHOLE capture
with it, wheel walk included (last good run: 16 Sep). No run_log row said so;
the only trace was a usage error in a local log file.

**Lesson for both lanes, same class as "Lambda ran stale code": merging to
origin/main does NOT deploy a Mac lane — the local checkout does. Never point
a plist at a flag/code the local checkout does not yet have; check
`git -C <repo> merge-base --is-ancestor <commit> live-main` first.**

Self-heals Monday 22:15 (live-main now has the code). Sentry's Monday-night
beat (Tue 00:45) verifies both DTEs per root landed.

### Monday verification points (sentry automates all three)
1. 16:20 beat: swing SELLs (ARWR, SNOW) raised IN-session and filled.
2. 16:20 beat: strangle placement verdicts present (placed True/False, zero NULL).
3. Tue 00:45 beat: ^SPX/^XSP/^NDX captured at BOTH DTEs for Monday.


## 2026-09-19 (later): THE MAC NOW DEPLOYS FROM MAIN — owner directive

Owner: "can we ensure our code always runs from main or deployed from main."
AWS already did (push → CI → ECR/Lambda). The Mac did not: ~29 launchd lanes
ran from the DEVELOPMENT checkout on live-main — whatever state it held. That
is what killed chain capture on 17-18 Sep (plist flag before the checkout had
the code).

**New topology, live as of today:**
* `~/tradepro-deploy` — a clone that only ever equals origin/main. ALL
  production plists now point here, not at ~/sourcecode/tradepro/tradepro.
* `com.tradepro.mac-deploy-sync` (every 10 min): fetch; if main moved,
  hard-reset + uv prewarm; one loud run_log row per deploy (process
  `mac-deploy`), silent when nothing changed. A dirty deploy clone posts
  status=error and resets anyway.
* **MERGING TO ORIGIN/MAIN IS NOW THE ONLY DEPLOY, ON EVERY SURFACE.**

⚠ FOR THE OTHER SESSION: editing code in ~/sourcecode/... no longer changes
what any lane runs — it deploys ~10 min after your merge reaches origin/main.
The dev checkout is now a pure workspace. Plist TEMPLATES in strategies/
scripts/ are repointed; installed ones under ~/Library/LaunchAgents were
sed-repointed in place (their args preserved — paper-swing-ibkr keeps
--capital-usd 150000, capture keeps --strangle-dte 7,21).

Also this session: owner DROPPED the change-freeze idea (right call — the
sentry + main-only deploys address the failure classes precisely); wheel
label question answered on the thread; swing/watch lane RENAME pending an
owner naming decision.


### Lane RENAMES (owner decision, 19 Sep): say what they are
* `signal-watch` → **`trade-alerts`** — it alerts when a candidate/position
  needs ACTION (stop breached, target hit). CLI `tradepro-trade-alerts`,
  label `com.tradepro.trade-alerts`, module `cli/trade_alerts.py`.
  State file KEEPS its old name (`~/.tradepro/signal_watch_fired.json`) — it
  is the dedupe ledger of every alert ever sent; a fresh path re-fires all.
  `fetched_by` provenance tag renamed forward; old rows keep the old tag.
* `paper-watch` → **`paper-job-runner`** — it never watched anything; it is
  the queue worker polling for paper-session trigger requests. CLI
  `tradepro-paper-job-runner` (module stays `paper_daemon.py`).
* Unchanged, accurately named: `swing-candidates`, `paper-swing-ibkr`,
  `preearnings-watch`.


## 2026-09-20 (RESEARCH): the 1m "broken" row — root causes found, both fixed

The desk's "Intraday bars (1m): broken 75h" was real, and the diagnosis is
NOT "IBKR got slow". Two compounding faults in bar-cache-resource-intraday:

1. **A monthly sawtooth by design.** The lane force-refreshed the whole
   MONTH-TO-DATE every night, so the workload grew linearly with the calendar
   until it crossed the fixed 90-min budget around day 17-20 of EVERY month.
   The kills cluster exactly there: nightly 19-28 Aug, again from 17 Sep.
   Day 16 took 58 min; day 21 took 81; day 18+ always died. Fix: weeknights
   re-source a TRAILING 7 days (~25 min, bounded forever); Saturdays run the
   28-day full sweep (wider budgets, no market to serve) which also covers
   the month-boundary days the old month-start scope never re-sourced.

2. **Sleep defeated the deadline.** `run_bounded` counted awake-time only, so
   a 22:00 run that slept overnight resumed on wake with its budget barely
   touched — and held the single IBKR OAuth session until 11:18 (26 Aug) and
   20:08 (28 Aug), straight through the trading day the guard exists to
   protect. Now three limits: awake budget, wall-clock (4h default), and an
   explicit weekday 13:15-20:05Z RTH cutoff. All three behaviour-tested.

Verification: Monday 22:00 run should complete in ~25 min; the desk's 1m row
goes current Tuesday morning. First Saturday sweep 26 Sep 11:00.


## 2026-09-20 evening (RESEARCH): Sunday triage — owner saw red, here is what each was

* **ibkr-health FAIL/DEGRADED rows**: Saturday-night gateway maintenance
  ("no bridge" 23:05 Sat) — routine, self-healed. The `fill read UNPRIMED`
  flaps (18-20 Sep only) correlate with an IDLE book (zero orders since
  16 Sep) + weekend; the canary retries less than the production reconcile
  path (4x/600ms). Tested live 20 Sep ~16:30Z: diagnose-fills answers
  **snapshot:true, primed**. NOT dismissed: if UNPRIMED shows during Monday
  RTH with real orders on, treat as P0 fill-blindness immediately.
* **bar-cache-harvest FAIL "1 symbol missing" x3**: WBS — DELISTED (Yahoo says
  so; our own 1d bars stop at 2026-08). It was never in the committed
  universe; it leaked in via its leftover store directory (harvest = universe
  ∪ store). Directory MOVED to bar_cache_quarantine/WBS_delisted_2026-09-20
  (reversible; S3 mirror keeps everything). Harvest set 968 → 967, fail gone.
* **968-symbol 5m runs**: deliberate — DATA lane widened the traded universe
  244 → 956 (717bfcc) today. Not a lane blowout; 944/968 came back GOLD.
* **1m + options screen rows stay red until Mon/Tue by design** — fixes are
  in, first proving runs are Mon 16:00 (screen) and Mon 22:00 (1m, trailing
  window). Do not re-diagnose them from the Sunday board.
* Owner Q&A recorded: closed-market option data is AVAILABLE but not
  TRUSTWORTHY for math (OI publishes late; closed-market marks violate
  parity — measured C-P=10.64 vs ~0.18 on ^XSP). Capture stays in the
  post-close window; boards may display last capture with an age label.


## 2026-09-20 night (RESEARCH): the swing board mixed THREE bar vintages — gated

Owner double-checked the swing signals and was right to. The 20 Sep board's 12
"BUY today" rows sat on three different closes presented as comparable:
CVS/GM/VZ on Friday 18 Sep, eight names on Thursday 17 Sep, and **PYPL on
31 AUGUST at -2.78σ**. Cause: the 244→956 widening seeded new names with
history ending at assorted dates; Friday's daily harvest ran on the OLD 244
before the widening; and the screen trusts each symbol's own last bar —
`_pick_signal_index` steps over PARTIAL bars but nothing checked AGE. The
footer even said "none were dropped" (suspect-SERIES guard ≠ freshness guard).

**Owner ruling, verbatim, now a standing rule: "core of our application shd be
justifiable and valid signal. better to not show anything rather than show
something with issues."**

Fix: freshness is a GATE. A row whose bar ≠ the settled session is REFUSED,
published in the artifact as `stale_dropped` (symbol, last_bar, settled,
reason) — same publish-your-own-work contract as `priced_out` — and the desk
header names the refused symbols. `signal_bar` is now THE session, never
rows[0]'s bar (a stale first row used to relabel the whole board). Monday's
956-wide daily harvest tops the new names up; they re-qualify only when
current.

Residual for DATA lane: PYPL (and possibly other widened names) have a
1-17 Sep daily-bar hole — check Monday's harvest window reaches back far
enough to fill seeded gaps, or the 20-day sigma window will straddle a hole
for names that pass the freshness gate on Tuesday.

---

## 21 Sep 2026 — EXITS UNBLOCKED, THEN RAN AWAY: swing is SHORT, daemon STOPPED

**`com.tradepro.paper-swing-ibkr` IS UNLOADED.** I stopped it at ~13:35Z. If you
find nothing running, that is why — not a crash. Re-loading it before the
position guard exists will resume the loop below.

**The book is SHORT on a long-only strategy:**

```
ARWR   -671 @ 66.41    notional 44,561   unrealised  +384.41
SNOW   -143 @ 332.26   notional 47,513   unrealised   -84.46
                               ─────────
                                92,073 USD short   (paper, DUP656969)
```

The other 13 positions are long and untouched.

**Cause, and it is mine.** `mean_reversion_swing_ibkr` had 0 of 47 exits reach
the broker — every SELL was BLOCKED by a `market_closed` gate in the C# RiskGate
(a SECOND copy of the rule; the strategy-side one was fixed 20 Sep and did
nothing on its own). PR #192 made that gate exempt exits on a trading day. It
worked — first exit fills the strategy has ever had:

```
IBKR order book:  ARWR  12 × SELL 61  all Filled  = 732 sold vs 61 held
                  SNOW  12 × SELL 13  all Filled  = 156 sold vs 13 held
```

**Nothing checks whether the position is already closed before re-raising the
exit.** The daemon has no position memory across its 15-minute restarts, so it
re-issues the same exit every cycle. While the gate blocked them this was
invisible; unblocking exposed the loop. The 47-attempts-for-12-positions ratio
was the warning and I read it as "exits are broken" rather than "exits repeat".

**NOT DONE — deliberately left for whoever owns this:**
1. **Flatten**: BUY 671 ARWR + 143 SNOW. I placed nothing; order placement is
   not something I will do unilaterally.
2. **The real guard**: the exit path must verify the BROKER position before
   raising a SELL. The strategy must be unable to sell stock it no longer
   holds, whatever any gate says. `allow_short=False` did not save us — that
   limit is evaluated against OMS state, and the OMS only knew about −61 of
   the −671. Broker is golden source; see project_broker_is_golden_source.
3. Do not re-enable the daemon until (2) exists.

Unrelated and still standing from today: the desk-check verdict now renders on
every desk view (PR #190) — it is what surfaced the 0/47 in the first place. Its
banner will read BROKEN until the 21:45 job recomputes.

---

## 24 Sep 2026 — STRANGLE: three mail/placement fixes SHIPPED; P&L trace STARTING (tradepro-7f)

**Deployed and verified**: Lambda `tradepro-jobs` reports `jobs_commit d73b616b0e48`,
which carries all three. All 15 EventBridge rules still hold their `{"job": ...}`
input (the blanking incident has NOT recurred).

- **PR #207 `index_strangle_eod.py`** — the EOD check mailed `[STRANGLE OK] clean`
  on 23 Sep while 3 of 4 units never placed. Two faults: `elif placed is False
  and err: pass` graded any *reasoned* refusal as acceptable (a chain outage
  passes that trivially), and the loop filtered `expiry_kind == "monthly"` so
  weeklies — placing since 14 Sep — were never audited at all.
- **PR #208 `index_strangle_paper.py`** — `PLACE_UNITS` now `(("XSP","monthly"),)`.
  21 sessions of decision log: XSP monthly 11/13 = 85%, XSP weekly 4/6, SPX
  weekly 3/6, SPX monthly 6/13 with 3 outright broker margin rejections.
  Override with `TRADEPRO_STRANGLE_PLACE_UNITS`.
- **PR #211 `preearnings_watch.py`** — a do-not-chase mail for MRVL never said
  MRVL was on the watch list, and "wait for its pullback zone" withheld the
  number the desk had already computed (239.53, 8.9% below).

**TWO THINGS A REFACTOR COULD SILENTLY UNDO** — flagging for whoever touches these:
1. The `PLACE_UNITS` gate is INSIDE `place_paper()` deliberately, not a filter on
   the caller's unit list. Hoist it and stood-down units stop writing a decision
   row; the EOD check then reads the missing row as "no placement attempt
   recorded at all" — a false alarm every session.
2. `"not in the placement set"` is matched by `_EXPECTED_REFUSALS` in
   `index_strangle_eod.py`. Reword it in one file only and every session goes red.
   Two files, one string.

**CLAIMING (trace in progress, may edit)**: `index_strangle_close.py`,
`index_strangle_paper.py` (record_execution / push_decisions), and whatever
writes `realised_pnl`. Chasing the P&L reconciliation break: credit − exit vs
`realised_pnl` diverged from 14 Sep, the same day BOTH expiries began placing.
Working hypothesis is two round-trips folded into one row. Will append findings.

### 24 Sep — P&L TRACE RESULT: two bugs, and the reported money was never wrong

**The headline: realised P&L is TRUSTWORTHY.** `/api/strangle-decisions/pnl`
reads `strangle_execution`, whose writer was corrected on 14 Sep to match
strikes. −1,812.52 over 10 closed pairs in 14 days stands. What was broken is
the DECISION LOG — the audit trail the EOD check, the desk board and any
by-hand query read.

**BUG A — the exit landed on the wrong expiry row.** `index_strangle_close.py`
hardcoded `expiryKind="monthly"` on every close. From the live log, 14 Sep:

    SPX weekly   placed=true   credit 2,866.74   exit —          realised —
    SPX MONTHLY  placed=FALSE  credit —          exit 2,691.63   realised 175.11

2,866.74 − 2,691.63 = 175.11 to the cent — right arithmetic, wrong row. Same
15 and 21 Sep. **Before 14 Sep only monthly placed, so the hardcoded literal
was ACCIDENTALLY CORRECT and every row reconciled at exactly 1.00x.** That
1.00x was evidence of nothing and read as proof.

Someone fixed this on 14 Sep — in the `strangle_execution` writer, which now
matches strikes and 409s loudly. The `strangle_decision_log` UPDATE twenty
lines above it in the same file was left keying on `expiry_kind`. **One fix,
two write sites, one applied.** Put this next to "grep the VALUE not the name".

**BUG B — a half pair recorded as the pair's credit.** `_credit_from_broker`
summed whatever legs the broker returned. On 23 Sep the call came back and the
put did not, so XSP recorded `credit_actual` 284.78 (the call alone, 2.847797 ×
100) against an exit of 981.19 — a round trip that lost 69.63 reading as
−696.41. The close was unaffected (it used the true 911.56), so realised was
right and the credit beside it was wrong by a factor of ten with nothing
saying so. The per-leg prices already followed the right rule; the TOTAL did
not. Now returns NULL unless both legs are seen — and the EOD check already
reports a NULL credit_actual, so the gap surfaces instead of hiding.

**NOT DONE — owner's call, deliberately not taken unilaterally.** Three
sessions (14, 15, 21 Sep) have exits filed against the wrong decision row.
`strangle_execution` holds the correct attribution and could drive a backfill.
I have not rewritten historical money records; flagging rather than doing.

**BAR-CACHE AUDIT — RECONCILED (third revision), and ONE REAL BAD BAR.**

Revised twice because the first two versions were wrong. Leaving the trail
rather than tidying it.

CAUSE OF THE BAD FIGURES: the audit log is APPEND-ONLY (the plist redirects
with `>>`) and held FOUR runs — summary lines at 1895, 3790, 5689, 8338. A
reader that greps the whole file merges four runs into one. The per-run
arithmetic was right all along; the SCOPE was wrong. Note that a
total-vs-breakdown assertion would NOT have caught this: each run was
internally consistent. What catches it is a run-id header and a reader that
scopes to the last run. Both belong in `bar_cache_audit.py`.

SCOPED TO THE LAST RUN, everything reconciles exactly:

    2,645 findings = 1,989 zero-volume + 655 stale + 1 spike
    27 distinct symbols · all 1d · 100 findings since 2024 (NOT 16)

AND THE CONCLUSION INVERTS: of the 27 findings dated 2026, TWENTY-SIX are FALSE
POSITIVES — a zero-volume test fired at ^VIX, ^TNX, PL=F, PA=F, instruments
that do not report volume at all. ^TNX alone is 33 of the 100 post-2024. The
audit's own noise floor is most of what looked like signal, which is the
strongest argument against running `--quarantine` on it. The volume test must
not fire on instruments that have no volume.

**THE 27th IS REAL, AND IT IS THE ONE THING HERE WORTH ACTING ON.** Verified
independently out of the parquet store (`~/.tradepro/bar_cache/us_etf/OKE/1d/
2026-09.parquet`), not taken on report:

    2026-09-08     96.10    97.83    95.00    97.51   1,528,117
    2026-09-09   1635.00  1635.00  1635.00  1635.00           0   <-- SYNTHETIC
    2026-09-10     96.67    96.67    94.96    95.82   1,061,526

Open = high = low = close = 1635.00 on ZERO volume. That is not a bad tick — a
bad tick moves one field. A bar with four identical prices and no volume is a
fabricated row. 16.8x the neighbouring closes, dated THIS MONTH, in the store
the STRATEGIES read (not the postgres one the charts read), on a name inside
the 956 universe, sitting inside both the 20-day and the 200-day windows today.
This is the shape of [[project_garbage_bar_false_buy]].

IMPACT IS NOT MEASURED and is tradepro-ef's, mid-flight. One thing to hold
loosely until they finish: the arithmetic points toward SUPPRESSION rather than
a false BUY — a 1635 in a 20-bar window inflates both the mean and sigma
enormously, so a −2.25σ entry becomes unreachable, and a 200-SMA lifted ~7.7
would push price below its own trend floor. A name that silently STOPS
qualifying is harder to notice than one that wrongly fires. That is reasoning,
not a result.

DO NOT silently repair or drop that row. It is one obvious-looking fix and
exactly the kind that reappears as an unexplained backtest change six weeks on.
Quarantine with a record, or an owner decision.
