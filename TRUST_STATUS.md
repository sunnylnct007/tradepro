# TradePro — Trust Status

**Last updated:** 2026-05-21 (Decide-page surfaces)

Per-metric audit of every number a user sees on the Decide page. Each
metric is graded **🟢 Green / 🟡 Yellow / 🔴 Red** against this rubric:

| Grade | Meaning |
|---|---|
| 🟢 Green | Source verified · regression test exists · at least one bug-and-fix cycle survived · sensible to act on |
| 🟡 Yellow | Source known and code is correct under inspection · NO regression test OR recently changed OR a known caveat the user must read · use as input not decision |
| 🔴 Red | Known issue · don't act on this number until it moves to Yellow |

**Honest baseline today (2026-05-21):** the user cannot rely on TradePro
for unsupervised trading decisions. The verdict columns + bucketing
logic are the most-trusted layer; everything peripheral (R/R, swing
score under specific regimes, analyst feed) has gaps.

---

## Decide page — verdict layer

### Verdict / bucket (BUY / WAIT / AVOID)
🟡 **Yellow.** Authoritative pricing-rules layer; survived two
high-impact bug cycles (BABA trend-coherence 2026-05-20, BUY-near-
highs range-guard 2026-04). Currently the most-tested number on the
page. Caveats:
- Two reviewer-flagged miscategorisations in the last 30 days. Pattern
  suggests there are more edge cases not yet surfaced.
- Doesn't factor in upcoming earnings (no flatten-before-earnings rule).
- Uses Yahoo daily-close prices — stale or wrong adj_close cascades
  into a wrong verdict.

**Promote to Green when:** earnings-blackout rule lands AND multi-source
price consensus (Phase 6.8) is on.

Source: `strategies/tradepro_strategies/compare.py:compute_bucket`
Regression: `features/market_state_classify.feature` (8 scenarios)

### Top candidate card
🟢 **Green** (the *label*, not the recommendation).
After 2026-05-21 fix, when there's no BUY, the card explicitly labels
itself "Watchlist · closest to a buy (still WAIT/AVOID)" with an
italic disclaimer. The underlying symbol selection is just `bestRow`
sort — trivially correct.

Source: `frontend/src/pages/Compare.tsx:VerdictHeadline`

### Per-strategy "Long / Flat / Short" cell
🟢 **Green.** Direct read of the strategy's last-emitted signal.
Each of the 5 base strategies has its own regression coverage
(`features/*.feature` — sma_crossover, rsi_mean_reversion,
macd_signal_cross, donchian_breakout, ichimoku_cloud,
bollinger_bounce). Buy-and-Hold trivially correct.

---

## Decide page — composite scores

### Swing score (0-8)
🟡 **Yellow.** Pure aggregation of 4 sub-scores (quality / valuation /
event / price). The math is correct. But:
- Sub-scores depend on upstream data: weak/missing Sharpe → quality
  layer is 0. Weak/missing earnings → event layer is 0. Quietly
  scoring 0 vs explicitly "no data" hides a coverage gap.
- The `evaluate_swing` fallback path in horizons was buggy until
  2026-05-20 (Bug #11 fixture fix). Anyone reading the swing column
  in the last few weeks may have been seeing wrong numbers when
  composite-score data was incomplete.

**Promote to Green when:** missing-data states render as "—" instead
of silent 0.

Source: `strategies/tradepro_strategies/swing.py`
Regression: `features/horizons.feature` (6 scenarios)

### Best sharpe / cagr
🟢 **Green.** Computed from adj_close return series. Straightforward;
all 5y backtest stats use the same pipeline. No known bugs.

Source: `strategies/tradepro_strategies/stats.py`

---

## Decide page — price target / risk-reward (Ichimoku)

### Price target · stop · R/R
🟡 **Yellow** (after 2026-05-21 fix).
The "→ X · stop Y · R/R Z×" sub-row.
- Previously surfaced "R/R -4.0×" on MTUM (price already above the
  cloud → senkou_b sits below as support, not a target → negative
  reward → meaningless ratio). Fixed by nulling target + R/R when
  reward would be ≤ 0. Pinned with a regression scenario.
- **Caveat:** for ABOVE-cloud setups (the common bullish case),
  TradePro now shows NO target. That's honest but not actionable.
  A projection-based target (entry + 2× cloud-thickness or similar)
  would unblock this.

**Promote to Green when:** ABOVE-cloud setups show a sensible
projection-based target with documented methodology.

Source: `strategies/tradepro_strategies/strategies/ichimoku_cloud.py:ichimoku_targets`
Regression: `features/ichimoku.feature` (5 scenarios)

---

## Decide page — context badges

### "Xd stale" badge
🟢 **Green.** Reads `bestRow.data_age_days` directly. Trivially
correct. Threshold at 7 days is conservative.

### Universe pills + counts
🟢 **Green.** Just `filter().length`. No room for error.

### Sentiment-demotion banner
🟡 **Yellow.** Logic is correct (rule fires only when mean +
material-negative-count both clear thresholds). But:
- News coverage on UK / EU stocks is patchy via Finnhub — the
  demotion CAN fail to fire on real bad-news days simply because
  the news wasn't seen.
- No regression test pinning the exact thresholds.

---

## Research / Signals page

### Per-strategy scan (BUY / SELL / HOLD)
🟡 **Yellow** (after 2026-05-21 fix).
- Was showing "7 failed" on every strategy when Yahoo 404'd a typo'd
  ticker (VGGS instead of VGGS.L). Fixed: 404 now degrades to HOLD
  with "No data returned by the provider" reason.
- Symbol-resolution layer ("VGGS → VGGS.L?") not yet built — typing
  a UK ticker without `.L` still produces HOLD across all strategies.

### Price-history chart
🟡 **Yellow.** Loads via the same provider chain as the matrix. No
known issues but no regression coverage.

---

## Order flow — Approve / Reject

### Pending orders queue
🟢 **Green.** Postgres-backed (`pending_orders` table). Receives
intents from Mac engine in manual mode. State machine
Pending→Placed/Failed/Rejected enforced by SQL CHECK.

### Approve → T212 demo
🟡 **Yellow** (after 2026-05-21 fix).
- Approve was throwing JSON-parse errors on every call. The
  Trading212{,Demo}Client was parsing a TRUNCATED snippet (with "…")
  and crashing at byte 203. Fixed: parse full body, snip only for
  logs.
- Fill-price reconciliation: today we record the bar-at-emit close
  as the fill price, NOT T212's real fill. Likely off by a few bps
  on liquid names; potentially material on illiquid ones.

**Promote to Green when:** T212 order-stream subscription lands so
recorded fills match broker fills exactly.

### Approve → T212 LIVE
⚠️ Not in the user-facing flow today (demo-only path is wired). When
this lights up it will need its own row.

---

## Intraday automation (Task #69)

### Continuous-mode engine
🟡 **Yellow.** End-to-end claim → run → complete roundtrip verified
against the live API on 2026-05-20. But:
- Every order queues as Pending (manual mode), nothing reaches T212.
  By design until the pre-trade gate enforcement lands.
- Yahoo intraday bars are unreliable — smoke test got 0 bars for
  AAPL during US market hours. Polygon.io intraday (Phase 7.2) is
  the unblocker.

### Strategy leaderboard
🟡 **Yellow.** Just shipped today. SQL rollup is direct; tested by
hand against the one completed session. No regression coverage yet.

---

## What I'd act on today (TL;DR)

If you asked me which numbers I'd personally trust to inform a real
trade today, with the bar set at "would I bet £500 of my own money
on this":

| Surface | Bet £500? | Why |
|---|---|---|
| Per-strategy long/flat/short | **Yes** | Direct backtest output; well-tested |
| Verdict (BUY / WAIT / AVOID) | **Yes — but verify the bucket reason** | Recently bug-cleared; visible reason is the audit trail |
| Top buy (when bucket = BUY) | **Yes** | Just a sort over verified verdicts |
| Best sharpe / cagr ranking | **Yes** | Straightforward math, stable inputs |
| Portfolio P&L (T212 columns) | **Yes** | T212's own numbers, displayed as-is |
| Decision trace (Section 3) | **Yes** | Ground-truth audit log of every rule fire |
| Conflict-surfacing UX (Section 4) | **Yes** | Pure UI over verified row data |
| Swing score | **Read but don't lead with it** | Sub-score data gaps hide as 0; coverage isn't visible |
| Portfolio "Today" column | **Read but spot-check** | Symbol-resolution failures show as "—" indistinguishable from "no universe match" |
| Price target / R/R | **No** | Even after fix, no target shown for the common bullish case |
| Sentiment-demotion banner | **Read as confirmation, not signal** | Coverage gaps on EU/UK |
| Analyst upgrades feed | **No** | Finnhub dropping events (task #71) |
| Intraday engine output | **No** | Manual mode only; bar data unreliable |
| Symbol Deep Dive Sections 8 / 9 | **N/A** | Placeholders — not built |

---

## Portfolio page

### T212 connection chip + mode badge
🟢 **Green.** Reads `resp.mode` direct from the integration health
endpoint. Error states (401, 403, network) render specific
remediation copy instead of silently claiming "no positions" (that
gaslighting bug is already fixed).

Source: `frontend/src/pages/Portfolio.tsx:ModeChip` + `backend/Providers/Trading212`

### Per-position quantity / avg cost / current price
🟢 **Green.** Reads T212's own portfolio endpoint verbatim. Numbers
are whatever the broker says they are — trustworthy unless T212 is
also wrong.

### P&L % and abs
🟢 **Green.** Same as above — calculated by T212, displayed as-is.

### Position-count total
🟢 **Green.** Trivially `positions.length`.

### Total unrealised
🟡 **Yellow.** Just `sum(p.unrealisedAbs ?? 0)`. Caveat: when
T212 returns a position with a null `unrealisedAbs` (it does for
fractional shares of certain ETFs, intermittently), it silently
contributes 0 instead of "—". Total can therefore understate by a
small amount.

### "Today" verdict per holding
🟡 **Yellow.** Just looks the position's symbol up in the best-
ranked cached universe. Verdict cell inherits the Decide page's
Yellow status. Specific Portfolio caveat:
- Symbol resolution: `p.yahooSymbol ?? p.ticker` — if Yahoo and T212
  disagree on the canonical symbol (common for ADRs, LSE-listed
  ETFs), the lookup misses and shows "—" even when a verdict exists.
- "—" today means "symbol not in any tracked universe" but ALSO
  hides the case "verdict exists but resolver failed". User can't
  tell which.

**Promote to Green when:** resolver disambiguates "no universe match"
from "symbol-mapping failure".

### "Swing" column
🟡 **Yellow.** Inherits Decide's swing-score caveats (sub-score
data gaps render as 0 silently). On Portfolio this gets surfaced
across every holding so the noise compounds.

---

## Symbol Deep Dive (page)

The page has 10 spec'd sections. Today 7 are real, 3 are placeholders.

### Section 1 — Header (symbol / price / current state)
🟡 **Yellow.** Reads from the same cached compare row that drives
Decide. Price + 52w-range + range_position trivially correct. But:
- Doesn't show how old the cache is — user can't tell if they're
  looking at today's number or yesterday's.

**Promote to Green when:** cache-age stamp shown.

### Section 2 — Verdict (big BUY/WAIT/AVOID badge)
🟡 **Yellow.** Same verdict logic as Decide; inherits the same
caveats. Plus: cross-strategy `in_position` count rendered next
to the badge — a useful tell that the badge here doesn't have on
the Decide page.

### Section 3 — Decision trace
🟢 **Green.** Just renders `row.market_state.decision_trace[]` —
each rule's pass/fail/warn label + detail. The TRACE is the
ground-truth audit log. If the verdict is wrong, the trace shows
where; if the trace is wrong, the upstream rule is buggy (and that
gets caught by the market_state regression suite).
This is the single most-trustworthy surface in the app.

### Section 4 — Strategy vote with explicit conflict surfacing
🟢 **Green** (the conflict-surfacing UX, not the data).
The whole "the long-term scorer says AVOID, the short-term says
BUY — here's why" framing is the moat. Conflict detection is pure
UI logic over the same row data the rest of the page uses.

### Section 5 — News + sentiment
🟡 **Yellow.** Latest headlines + per-item LLM sentiment score.
Caveats:
- Coverage gaps on UK / EU stocks (Finnhub thin outside US).
- Sentiment score is from one LLM pass, no human validation.
- No regression coverage on the news layer.

### Section 6 — Analyst consensus (stacked bar)
🔴 **Red on the upgrades feed.** Reviewer 2026-05-20 flagged
Finnhub dropping upgrade events for ADRs (BABA upgrade missed).
Task #71 open to audit + fix.
🟡 **Yellow on the static consensus.** Strong-buy / buy / hold /
sell counts + mean target price look correct against TipRanks for
the symbols I've spot-checked, but no automated cross-check.

### Section 7 — Earnings event risk
🟡 **Yellow.** Shows next earnings date when present in the row.
But:
- Forward calendar is sparse — task #66 (forward earnings on every
  row) hasn't shipped.
- No "flatten before earnings" rule wired in.

### Section 8 — Regime survival
🔴 **Not built.** Placeholder; needs task #66 backend prep.

### Section 9 — Peer comparison
🔴 **Not built.** Placeholder; needs task #66 (symbol → tags map).

### Section 10 — Hit rate
🟡 **Yellow.** Per-strategy historical accuracy on this symbol.
Fires parallel `/api/signals/hitrate` calls per strategy and sorts
by Sharpe. Caveats:
- Hit rate horizon is configurable but no documented choice on
  what's the "right" lookback (task #67 open question).
- No regression test pinning the hit-rate math.

---

## In-progress, not yet in this audit

Pages that exist but haven't been audited line-by-line:
- Backtest / Simulations
- Paper page (live tab)
- Charts
- Settings (just shipped Intraday block)
- Intraday leaderboard (just shipped)

Next audit pass: Backtest + Paper page + Settings. Then a UI pass to
make the grades visible inline (small dots next to each metric).
